"""Authentication service for LinkedIn session management."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import uuid4

from playwright.sync_api import BrowserContext, Error, Locator, Page, TimeoutError, sync_playwright

from app.config import settings
from app.utils.file_utils import ensure_directory, save_json_file_overwrite
from app.utils.logger import get_logger


logger = get_logger(__name__)

EMAIL_SELECTORS = [
    'input[name="session_key"]',
    'input#username',
    'input#email-or-phone',
    'input[name="username"]',
    'input[autocomplete="username"]',
    'input[type="email"]',
    'input[type="text"]',
]
PASSWORD_SELECTORS = [
    'input[name="session_password"]',
    'input#password',
    'input#password-input',
    'input[name="password"]',
    'input[autocomplete="current-password"]',
    'input[type="password"]',
]
SUBMIT_SELECTORS = [
    'button[type="submit"]',
    'button[data-litms-control-urn="login-submit"]',
    'button._0290c384:has-text("Sign in")',
    'button:has-text("Sign in")',
    'button:has-text("Log in")',
]

OTP_INPUT_SELECTOR = "#input__email_verification_pin"
OTP_SUBMIT_SELECTOR = "#email-pin-submit-button"
PENDING_LOGIN_TTL_SEC = 900


class PendingLoginSessionNotFoundError(RuntimeError):
    """Raised when pending OTP session is missing/expired."""


@dataclass
class PendingLoginSession:
    """In-memory session used between /login and /verify."""

    pending_session_id: str
    normalized_session_id: str
    email: str
    state_path: Path
    checkpoint_url: str
    playwright: Any
    browser: Any
    context: BrowserContext
    page: Page
    created_at: float


@dataclass
class LoginFlowResult:
    """Structured result for 2-step login flow."""

    status: Literal["success", "need_otp"]
    session_id: str
    state_path: Path | None
    email: str
    checkpoint_url: str | None = None


@dataclass
class SessionStatusResult:
    """Status of a saved LinkedIn storage-state file."""

    email: str | None
    session_id: str
    state_path: Path
    exists: bool
    has_auth_cookie: bool
    valid: bool
    needs_login: bool
    live_checked: bool = False
    message: str = ""


_pending_login_sessions: dict[str, PendingLoginSession] = {}
_pending_login_lock = threading.Lock()


def normalize_session_id(session_id: str | None) -> str:
    """Normalize a user-supplied session ID into a filesystem-safe value."""

    raw_value = (session_id or "").strip().lower()
    if not raw_value:
        raw_value = uuid4().hex

    normalized = re.sub(r"[^a-z0-9_-]+", "-", raw_value).strip("-_")
    if not normalized:
        normalized = uuid4().hex
    return normalized


def email_to_session_basename(email: str) -> str:
    """Chuyển email thành tên file an toàn (stem .json) — mỗi user một file session riêng.

    Ví dụ: ``user.name@gmail.com`` → ``user_name_gmail_com`` → file ``user_name_gmail_com.json``.
    """

    normalized = (email or "").strip().lower()
    if not normalized:
        return f"session_{uuid4().hex[:12]}"

    try:
        ascii_like = unicodedata.normalize("NFKD", normalized).encode("ascii", "ignore").decode("ascii")
    except Exception:
        ascii_like = normalized

    slug = re.sub(r"[^a-z0-9]+", "_", ascii_like).strip("_")
    if not slug:
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
        return f"user_{digest}"

    if len(slug) > 200:
        slug = slug[:200].rstrip("_")

    return slug


def default_session_id_for_email(email: str) -> str:
    """Tên session (stem file) ổn định theo email — khớp với file ``{stem}.json`` sau khi login."""

    return email_to_session_basename(email)


def resolve_session_id(session_id: str | None, email: str | None = None) -> str:
    """Resolve a stable session ID from explicit input or email fallback."""

    if session_id and session_id.strip():
        return normalize_session_id(session_id)
    if email and email.strip():
        return default_session_id_for_email(email)
    return normalize_session_id(None)


def build_session_state_path(session_id: str | None, email: str | None = None) -> tuple[str, Path]:
    """Build a resolved session ID and the matching storage-state path."""

    normalized_session_id = resolve_session_id(session_id=session_id, email=email)
    ensure_directory(settings.session_storage_dir)
    return normalized_session_id, settings.session_storage_dir / f"{normalized_session_id}.json"


def build_session_profile_dir(session_id: str | None, email: str | None = None) -> tuple[str, Path]:
    """Build a resolved session ID and the matching persistent browser profile directory."""

    normalized_session_id = resolve_session_id(session_id=session_id, email=email)
    ensure_directory(settings.session_profile_dir)
    return normalized_session_id, settings.session_profile_dir / normalized_session_id


def build_session_metadata_path(session_id: str | None, email: str | None = None) -> Path:
    """Build the path for session metadata (like full name)."""
    normalized_session_id = resolve_session_id(session_id=session_id, email=email)
    return settings.session_storage_dir / f"{normalized_session_id}.metadata.json"


def _scrape_user_full_name(page: Page) -> str | None:
    """Attempt to scrape the user's full name from the LinkedIn feed page."""
    try:
        # Try multiple common selectors for the user's name in the left sidebar or top nav
        selectors = [
            ".t-16.t-black.t-bold", # Name in sidebar
            ".feed-identity-module__name",
            "div.t-16.t-black.t-bold",
            "a[href*='/in/'] div.t-16",
        ]
        for selector in selectors:
            name_elem = page.locator(selector).first
            if name_elem.count() > 0 and name_elem.is_visible(timeout=2000):
                name = name_elem.inner_text().strip()
                if name:
                    logger.info(f"Found user full name: {name}")
                    return name
        return None
    except Exception as e:
        logger.debug(f"Failed to scrape user name: {e}")
        return None


def _is_authwall_url(current_url: str) -> bool:
    """Return True when URL points to a LinkedIn auth gate."""

    parsed = urlparse((current_url or "").strip())
    path = (parsed.path or "").lower()
    return any(path.startswith(prefix) for prefix in ["/login", "/checkpoint", "/authwall"])


def _has_li_at_cookie(storage_state: dict) -> bool:
    """Check whether storage state contains LinkedIn auth cookie li_at."""

    cookies = storage_state.get("cookies") if isinstance(storage_state, dict) else None
    if not isinstance(cookies, list):
        return False

    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        if str(cookie.get("name", "")).strip() == "li_at" and str(cookie.get("value", "")).strip():
            return True
    return False


def _capture_login_artifacts(page: Page, filename_prefix: str) -> None:
    """Save screenshot and HTML to help debug login failures."""

    ensure_directory(settings.raw_data_dir)
    screenshot_path = settings.raw_data_dir / f"{filename_prefix}.png"
    html_path = settings.raw_data_dir / f"{filename_prefix}.html"

    try:
        if page.is_closed():
            html_path.write_text(
                "<html><body><p>Playwright page was already closed before debug artifacts could be captured.</p></body></html>",
                encoding="utf-8",
            )
            logger.warning("Skipped login screenshot because the page was already closed")
            return
    except Error:
        logger.debug("Could not determine whether login page is closed", exc_info=True)

    try:
        page.screenshot(path=str(screenshot_path), full_page=True)
    except Error:
        logger.warning("Could not capture login screenshot because page/context/browser was already closed")

    try:
        html_path.write_text(page.content(), encoding="utf-8")
    except Error:
        html_path.write_text(
            "<html><body><p>Could not capture page HTML because page/context/browser was already closed.</p></body></html>",
            encoding="utf-8",
        )
        logger.warning("Could not capture login HTML because page/context/browser was already closed")


def _first_visible_locator(page: Page, selectors: list[str], timeout_ms: int = 4000) -> Locator | None:
    """Return the first selector whose element becomes visible."""

    for selector in selectors:
        locator = page.locator(selector).first
        try:
            locator.wait_for(state="visible", timeout=timeout_ms)
            return locator
        except TimeoutError:
            continue
        except Error:
            logger.debug("Selector lookup failed for %s", selector, exc_info=True)
    return None


def _first_visible_descendant(container: Locator | Page, selectors: list[str], timeout_ms: int = 3000) -> Locator | None:
    """Return the first visible matching descendant across all selector candidates."""

    for selector in selectors:
        try:
            locator = container.locator(selector)
            count = locator.count()
            for index in range(count):
                candidate = locator.nth(index)
                try:
                    candidate.wait_for(state="visible", timeout=timeout_ms)
                    if candidate.is_visible():
                        return candidate
                except (TimeoutError, Error):
                    continue
        except Error:
            logger.debug("Descendant selector lookup failed for %s", selector, exc_info=True)
    return None


def _find_email_input(page: Page) -> Locator | None:
    """Find the email field using selectors and accessible labels."""

    locator = _first_visible_locator(page, EMAIL_SELECTORS, timeout_ms=5000)
    if locator is not None:
        return locator

    candidate = _first_visible_descendant(
        page,
        ['input[id^=":r"][type="text"]', 'input[aria-describedby*="info"][type="text"]', 'input[type="text"]'],
    )
    if candidate is not None:
        return candidate

    for label_text in ["Email or phone", "Email", "Phone"]:
        try:
            label_locator = page.get_by_label(label_text, exact=False)
            for index in range(label_locator.count()):
                candidate = label_locator.nth(index)
                try:
                    candidate.wait_for(state="visible", timeout=3000)
                    if candidate.is_visible():
                        return candidate
                except (TimeoutError, Error):
                    continue
        except (TimeoutError, Error):
            logger.debug("Could not find email field by label %s", label_text, exc_info=True)
    return None


def _find_password_input(page: Page) -> Locator | None:
    """Find the password field using selectors and accessible labels."""

    locator = _first_visible_locator(page, PASSWORD_SELECTORS, timeout_ms=5000)
    if locator is not None:
        return locator

    candidate = _first_visible_descendant(
        page,
        ['input[id^=":r"][type="password"]', 'input[autocomplete="current-password"]', 'input[type="password"]'],
    )
    if candidate is not None:
        return candidate

    try:
        label_locator = page.get_by_label("Password", exact=False)
        for index in range(label_locator.count()):
            candidate = label_locator.nth(index)
            try:
                candidate.wait_for(state="visible", timeout=3000)
                if candidate.is_visible():
                    return candidate
            except (TimeoutError, Error):
                continue
    except (TimeoutError, Error):
        logger.debug("Could not find password field by label", exc_info=True)
        return None
    return None


def _find_submit_button(page: Page) -> Locator | None:
    """Find the sign-in button using selectors and accessible names."""

    for selector in SUBMIT_SELECTORS:
        try:
            buttons = page.locator(selector)
            for index in range(buttons.count()):
                candidate = buttons.nth(index)
                try:
                    candidate.wait_for(state="visible", timeout=3000)
                    text = candidate.inner_text(timeout=1000).strip().lower()
                    if text in {"sign in", "log in"}:
                        return candidate
                except (TimeoutError, Error):
                    continue
        except Error:
            logger.debug("Could not find submit button in login root via %s", selector, exc_info=True)

    locator = _first_visible_locator(page, SUBMIT_SELECTORS, timeout_ms=3000)
    if locator is not None:
        try:
            text = locator.inner_text(timeout=1000).strip().lower()
            if text == "sign in" or text == "log in":
                return locator
        except Error:
            logger.debug("Could not read candidate submit button text", exc_info=True)

    for button_text in ["Sign in", "Log in"]:
        try:
            buttons = page.get_by_role("button", name=button_text, exact=True)
            for index in range(buttons.count()):
                candidate = buttons.nth(index)
                try:
                    candidate.wait_for(state="visible", timeout=3000)
                    if candidate.is_visible():
                        return candidate
                except (TimeoutError, Error):
                    continue
        except Error:
            logger.debug("Could not find submit button by role %s", button_text, exc_info=True)
    return None


def _set_input_value(locator: Locator, value: str) -> None:
    """Set input value and dispatch events so LinkedIn's React form picks it up."""

    locator.click(force=True)
    locator.fill("")
    locator.evaluate(
        """(element, nextValue) => {
            element.focus();
            const prototype = Object.getPrototypeOf(element);
            const descriptor = Object.getOwnPropertyDescriptor(prototype, 'value');
            if (descriptor && descriptor.set) {
                descriptor.set.call(element, nextValue);
            } else {
                element.value = nextValue;
            }
            element.dispatchEvent(new Event('input', { bubbles: true }));
            element.dispatchEvent(new Event('change', { bubbles: true }));
        }""",
        value,
    )


def _fill_login_form(page: Page, email: str, password: str, max_retries: int = 2) -> bool:
    """Find and fill the LinkedIn login form using resilient fallbacks.

    Returns True if form was found and filled successfully, False otherwise.
    Retries up to ``max_retries`` times with a short wait between attempts.
    """

    for attempt in range(1, max_retries + 1):
        email_input = _find_email_input(page)
        password_input = _find_password_input(page)

        if email_input is not None and password_input is not None:
            _set_input_value(email_input, email)
            _set_input_value(password_input, password)

            submit_button = _find_submit_button(page)
            if submit_button is not None:
                submit_button.click(force=True)
            else:
                password_input.press("Enter")
            return True

        logger.warning(
            "[Lần %s/%s] Không tìm thấy ô email/mật khẩu trên trang đăng nhập LinkedIn. "
            "Có thể LinkedIn đang hiển thị CAPTCHA hoặc giao diện mới.",
            attempt, max_retries,
        )
        if attempt < max_retries:
            try:
                page.wait_for_timeout(3000)
            except Error:
                pass

    return False


def _verify_state_file_contains_auth_cookie(state_path: Path) -> None:
    """Validate saved storage state contains the main LinkedIn auth cookie."""

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    if not _has_li_at_cookie(payload):
        raise RuntimeError(
            "Saved session is missing LinkedIn auth cookie (li_at). Relogin and complete verification steps."
        )


def _context_has_li_at_cookie(context: BrowserContext) -> bool:
    """Return True if the current browser context has li_at in storage state."""

    try:
        state = context.storage_state()
    except Error:
        logger.debug("Could not inspect context storage state", exc_info=True)
        return False
    return _has_li_at_cookie(state)


def _wait_for_login_session(page: Page, context: BrowserContext, timeout_ms: int = 45000) -> None:
    """Wait until LinkedIn session is established after submitting login form."""

    end_time = time.time() + (timeout_ms / 1000)

    while time.time() < end_time:
        if _context_has_li_at_cookie(context) and not _is_authwall_url(page.url):
            return

        try:
            page.wait_for_timeout(1000)
        except Error:
            logger.debug("Waiting for login session encountered a transient error", exc_info=True)

    _capture_login_artifacts(page, "login_session_not_ready")
    raise RuntimeError(
        "LinkedIn login did not produce a reusable session in time. "
        "Check data/raw/login_session_not_ready.html and .png."
    )


def _is_feed_or_group_url(current_url: str) -> bool:
    """Return True when URL is feed or a LinkedIn group page."""

    parsed = urlparse((current_url or "").strip())
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    if "linkedin.com" not in host:
        return False
    return path.startswith("/feed") or path.startswith("/groups")


def _wait_for_manual_verification_until_ready(
    page: Page,
    timeout_ms: int = 300000,
) -> None:
    """Wait for user to complete manual verification until leaving authwall pages."""

    logger.info("Vui lòng nhập mã/xác nhận trên trình duyệt")
    end_time = time.time() + (timeout_ms / 1000)
    while time.time() < end_time:
        if not _is_authwall_url(page.url):
            return
        try:
            page.wait_for_timeout(1000)
        except Error:
            logger.debug("Waiting for manual verification encountered a transient error", exc_info=True)

    _capture_login_artifacts(page, "login_verification_timeout")
    raise RuntimeError(
        "Đã chờ xác minh thủ công nhưng URL vẫn ở login/checkpoint/authwall. "
        "Check data/raw/login_verification_timeout.html and .png."
    )


def _existing_state_is_reusable(state_path: Path) -> bool:
    """Return True when existing state file contains a reusable auth cookie."""

    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Could not parse existing state file at %s; forcing relogin", state_path, exc_info=True)
        return False

    if not _has_li_at_cookie(payload):
        logger.warning("Existing state file at %s is missing li_at cookie; forcing relogin", state_path)
        return False

    return True


def _saved_state_has_auth_cookie(state_path: Path) -> bool:
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return _has_li_at_cookie(payload)


def _context_has_li_at_cookie(context: BrowserContext) -> bool:
    try:
        for cookie in context.cookies():
            if cookie.get("name") == "li_at" and str(cookie.get("value", "")).strip():
                return True
    except Exception:
        logger.debug("Could not inspect browser cookies while checking session", exc_info=True)
    return False


def _verify_saved_session_live_sync(state_path: Path, profile_dir: Path | None = None) -> bool:
    playwright = None
    browser = None
    context = None
    try:
        playwright = sync_playwright().start()
        launch_args = [
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-infobars",
        ]
        if settings.use_persistent_profile and profile_dir is not None and profile_dir.exists():
            context = playwright.chromium.launch_persistent_context(
                str(profile_dir),
                headless=True,
                args=launch_args,
            )
        else:
            browser = playwright.chromium.launch(
                headless=True,
                args=launch_args,
            )
            context = browser.new_context(storage_state=str(state_path))
        page = context.new_page()
        page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2000)
        return (not _is_authwall_url(page.url)) and _context_has_li_at_cookie(context)
    except Exception:
        logger.warning("Live LinkedIn session check failed for %s", state_path, exc_info=True)
        return False
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                logger.debug("Failed to close context after session check", exc_info=True)
        if browser is not None:
            try:
                browser.close()
            except Exception:
                logger.debug("Failed to close browser after session check", exc_info=True)
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                logger.debug("Failed to stop playwright after session check", exc_info=True)


def check_saved_session(
    *,
    email: str | None = None,
    session_id: str | None = None,
    verify_live: bool = False,
) -> SessionStatusResult:
    """Check whether a saved LinkedIn session can be used for crawling."""

    normalized_session_id, state_path = build_session_state_path(session_id=session_id, email=email)
    _, profile_dir = build_session_profile_dir(session_id=normalized_session_id, email=email)
    exists = state_path.exists()
    if not exists:
        return SessionStatusResult(
            email=email,
            session_id=normalized_session_id,
            state_path=state_path,
            exists=False,
            has_auth_cookie=False,
            valid=False,
            needs_login=True,
            live_checked=False,
            message="Chưa có session LinkedIn. Vui lòng đăng nhập ở mục Tài khoản.",
        )

    has_auth_cookie = _saved_state_has_auth_cookie(state_path)
    if not has_auth_cookie:
        return SessionStatusResult(
            email=email,
            session_id=normalized_session_id,
            state_path=state_path,
            exists=True,
            has_auth_cookie=False,
            valid=False,
            needs_login=True,
            live_checked=False,
            message="Session LinkedIn không hợp lệ. Vui lòng đăng nhập lại.",
        )

    if not verify_live:
        return SessionStatusResult(
            email=email,
            session_id=normalized_session_id,
            state_path=state_path,
            exists=True,
            has_auth_cookie=True,
            valid=True,
            needs_login=False,
            live_checked=False,
            message="Đã tìm thấy session LinkedIn có cookie đăng nhập.",
        )

    live = _verify_saved_session_live_sync(state_path, profile_dir)
    return SessionStatusResult(
        email=email,
        session_id=normalized_session_id,
        state_path=state_path,
        exists=True,
        has_auth_cookie=True,
        valid=live,
        needs_login=not live,
        live_checked=True,
        message=(
            "Session LinkedIn còn dùng được."
            if live
            else "Session LinkedIn đã hết hạn hoặc bị LinkedIn chặn. Vui lòng đăng nhập lại."
        ),
    )


def _is_checkpoint_challenge_url(current_url: str) -> bool:
    parsed = urlparse((current_url or "").strip())
    path = (parsed.path or "").lower()
    return path.startswith("/checkpoint/challenge")


def _close_pending_browser_objects(pending: PendingLoginSession) -> None:
    try:
        pending.context.close()
    except Exception:
        logger.debug("Failed to close pending context", exc_info=True)
    if pending.browser is not None:
        try:
            pending.browser.close()
        except Exception:
            logger.debug("Failed to close pending browser", exc_info=True)
    try:
        pending.playwright.stop()
    except Exception:
        logger.debug("Failed to stop pending playwright", exc_info=True)


def _cleanup_expired_pending_sessions() -> None:
    now = time.time()
    expired_ids: list[str] = []
    with _pending_login_lock:
        for sid, pending in _pending_login_sessions.items():
            if now - pending.created_at > PENDING_LOGIN_TTL_SEC:
                expired_ids.append(sid)
        for sid in expired_ids:
            pending = _pending_login_sessions.pop(sid, None)
            if pending is not None:
                _close_pending_browser_objects(pending)
    if expired_ids:
        logger.info("Cleaned up %s expired pending login session(s)", len(expired_ids))


def _register_pending_session(
    *,
    normalized_session_id: str,
    email: str,
    state_path: Path,
    checkpoint_url: str,
    playwright: Any,
    browser: Any,
    context: BrowserContext,
    page: Page,
) -> str:
    _cleanup_expired_pending_sessions()
    pending_session_id = uuid4().hex
    pending = PendingLoginSession(
        pending_session_id=pending_session_id,
        normalized_session_id=normalized_session_id,
        email=email,
        state_path=state_path,
        checkpoint_url=checkpoint_url,
        playwright=playwright,
        browser=browser,
        context=context,
        page=page,
        created_at=time.time(),
    )
    with _pending_login_lock:
        _pending_login_sessions[pending_session_id] = pending
    return pending_session_id


def _get_pending_session_or_raise(session_id: str) -> PendingLoginSession:
    _cleanup_expired_pending_sessions()
    with _pending_login_lock:
        pending = _pending_login_sessions.get((session_id or "").strip())
    if pending is None:
        raise PendingLoginSessionNotFoundError(
            "Session xác minh OTP không tồn tại hoặc đã hết hạn. Vui lòng login lại."
        )
    return pending


def _remove_pending_session(session_id: str) -> PendingLoginSession | None:
    with _pending_login_lock:
        return _pending_login_sessions.pop((session_id or "").strip(), None)


def _save_session_state(context: BrowserContext, state_path: Path) -> None:
    if state_path.exists():
        logger.info("Ghi đè session tại %s (file đã tồn tại)", state_path)
    save_json_file_overwrite(state_path, context.storage_state())
    _verify_state_file_contains_auth_cookie(state_path)
    logger.info("Saved LinkedIn storage state to %s", state_path)


def _login_and_save_session_sync(
    email: str,
    password: str,
    session_id: str | None = None,
    force_relogin: bool = True,
) -> LoginFlowResult:
    """Login to LinkedIn và lưu storage state vào đúng một file session (theo email / session_id).

    Sau khi đăng nhập trình duyệt thành công, luôn **ghi đè** file tại ``state_path`` — không tạo thêm
    file tên khác. Nếu ``force_relogin=False`` và file cũ còn dùng được thì trả về luôn (không chạy
    login lại, không ghi đè).
    """

    if not email.strip() or not password:
        raise ValueError("email and password are required in the request body")

    normalized_session_id, state_path = build_session_state_path(session_id=session_id, email=email)
    _, profile_dir = build_session_profile_dir(session_id=normalized_session_id, email=email)
    ensure_directory(state_path.parent)

    if state_path.exists() and not force_relogin:
        if _existing_state_is_reusable(state_path):
            if settings.use_persistent_profile and not profile_dir.exists():
                logger.info(
                    "Existing LinkedIn state file is reusable but persistent profile is missing; "
                    "continuing login to create profile at %s",
                    profile_dir,
                )
            else:
                logger.info("Reusing existing LinkedIn state file at %s", state_path)
                return LoginFlowResult(
                    status="success",
                    session_id=normalized_session_id,
                    state_path=state_path,
                    email=email.strip().lower(),
                )
        else:
            logger.info("Existing LinkedIn state file is invalid; continuing with fresh login")

    playwright = sync_playwright().start()
    browser = None
    launch_args = [
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-infobars",
    ]
    if settings.use_persistent_profile:
        ensure_directory(profile_dir)
        context = playwright.chromium.launch_persistent_context(
            str(profile_dir),
            headless=settings.headless,
            channel="chrome" if not settings.headless else None,
            args=launch_args,
        )
    else:
        browser = playwright.chromium.launch(
            headless=settings.headless,
            channel="chrome" if not settings.headless else None,
            args=launch_args,
        )
        context = browser.new_context()

    page = context.new_page()
    keep_open_for_verify = False

    try:
        logger.info("Opening LinkedIn login page")
        page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=120000)
        page.wait_for_timeout(3000)

        # Check if already logged in (user might have logged in manually or cookies exist)
        if _context_has_li_at_cookie(context) and not _is_authwall_url(page.url):
            logger.info("Detected existing session or manual login success; skipping form fill")
            _save_session_state(context, state_path)
            return LoginFlowResult(status="success", session_id=normalized_session_id, state_path=state_path, email=email)

        form_filled = _fill_login_form(page, email=email, password=password)

        if not form_filled:
            # Form không tìm thấy → không cần chờ 5-10 phút, đóng browser ngay và báo lỗi rõ ràng
            _capture_login_artifacts(page, "login_form_not_found")
            raise RuntimeError(
                "Không tìm thấy ô email/mật khẩu trên trang đăng nhập LinkedIn sau nhiều lần thử. "
                "LinkedIn có thể đang hiển thị CAPTCHA, giao diện mới, hoặc đang chặn đăng nhập tự động. "
                "Vui lòng thử lại sau vài phút. Screenshot: data/raw/login_form_not_found.png"
            )

        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except TimeoutError:
            logger.warning("Initial page transition after login click timed out; continuing to wait for session")

        if _is_checkpoint_challenge_url(page.url):
            pending_session_id = _register_pending_session(
                normalized_session_id=normalized_session_id,
                email=email.strip().lower(),
                state_path=state_path,
                checkpoint_url=page.url,
                playwright=playwright,
                browser=browser,
                context=context,
                page=page,
            )
            keep_open_for_verify = True
            logger.info("LinkedIn login requires OTP verification for session %s", pending_session_id)
            return LoginFlowResult(
                status="need_otp",
                session_id=pending_session_id,
                state_path=None,
                email=email.strip().lower(),
                checkpoint_url=page.url,
            )

        try:
            _wait_for_login_session(page, context, timeout_ms=45000)
        except RuntimeError:
            if _is_checkpoint_challenge_url(page.url):
                pending_session_id = _register_pending_session(
                    normalized_session_id=normalized_session_id,
                    email=email.strip().lower(),
                    state_path=state_path,
                    checkpoint_url=page.url,
                    playwright=playwright,
                    browser=browser,
                    context=context,
                    page=page,
                )
                keep_open_for_verify = True
                logger.info("LinkedIn login requires OTP verification for session %s", pending_session_id)
                return LoginFlowResult(
                    status="need_otp",
                    session_id=pending_session_id,
                    state_path=None,
                    email=email.strip().lower(),
                    checkpoint_url=page.url,
                )
            # Có thể cần xác minh thủ công (CAPTCHA, 2FA không qua OTP...)
            _wait_for_manual_verification_until_ready(page, timeout_ms=300000)

        if not _is_feed_or_group_url(page.url):
            # Chỉ đợi thủ công nếu URL còn ở authwall — không đợi nếu LinkedIn redirect về trang khác
            if _is_authwall_url(page.url):
                _wait_for_manual_verification_until_ready(page, timeout_ms=300000)

        if _is_authwall_url(page.url):
            _capture_login_artifacts(page, "login_blocked")
            raise RuntimeError(
                "LinkedIn login is still on login/checkpoint/authwall. This can happen during 2-step verification "
                "or account security checks. Complete verification manually and retry."
            )

        # Scrape full name before closing
        full_name = _scrape_user_full_name(page)
        if full_name:
            metadata_path = build_session_metadata_path(session_id=normalized_session_id, email=email)
            save_json_file_overwrite(metadata_path, {"full_name": full_name, "email": email, "updated_at": datetime.now().isoformat()})

        _save_session_state(context, state_path)
        return LoginFlowResult(
            status="success",
            session_id=normalized_session_id,
            state_path=state_path,
            email=email.strip().lower(),
        )
    except Error as exc:
        logger.exception("LinkedIn login failed")
        raise RuntimeError(f"LinkedIn login failed: {exc}") from exc
    except Exception:
        logger.exception("LinkedIn login failed")
        raise
    finally:
        if not keep_open_for_verify:
            context.close()
            if browser is not None:
                browser.close()
            playwright.stop()


def _verify_pending_login_otp_sync(
    pending_session_id: str,
    otp_code: str,
    checkpoint_url: str | None = None,
) -> tuple[str, Path, str]:
    """Submit OTP for a pending login session and persist the final state."""

    pending = _get_pending_session_or_raise(pending_session_id)
    otp_value = (otp_code or "").strip()
    if not otp_value:
        raise ValueError("otp is required")

    page = pending.page
    if page.is_closed():
        _remove_pending_session(pending.pending_session_id)
        _close_pending_browser_objects(pending)
        raise PendingLoginSessionNotFoundError(
            "Session xác minh OTP đã bị đóng. Vui lòng login lại."
        )

    try:
        target_url = (checkpoint_url or pending.checkpoint_url or "").strip()
        if target_url and page.url != target_url:
            page.goto(target_url, wait_until="domcontentloaded", timeout=120000)

        page.wait_for_selector(OTP_INPUT_SELECTOR, timeout=30000)
        page.fill(OTP_INPUT_SELECTOR, otp_value)
        page.click(OTP_SUBMIT_SELECTOR)

        end_time = time.time() + 120
        while time.time() < end_time:
            if _context_has_li_at_cookie(pending.context) and not _is_authwall_url(page.url):
                # Scrape name after OTP success
                full_name = _scrape_user_full_name(page)
                if full_name:
                    metadata_path = build_session_metadata_path(session_id=pending.normalized_session_id, email=pending.email)
                    save_json_file_overwrite(metadata_path, {"full_name": full_name, "email": pending.email, "updated_at": datetime.now().isoformat()})

                _save_session_state(pending.context, pending.state_path)
                _remove_pending_session(pending.pending_session_id)
                _close_pending_browser_objects(pending)
                return pending.normalized_session_id, pending.state_path, pending.email
            try:
                page.wait_for_timeout(1000)
            except Error:
                logger.debug("Waiting OTP verification encountered a transient error", exc_info=True)

        raise RuntimeError(
            "Xác minh OTP chưa hoàn tất. Vui lòng kiểm tra mã và thử lại."
        )
    except Error as exc:
        raise RuntimeError(f"LinkedIn OTP verification failed: {exc}") from exc


def _scrape_user_full_name(page: Page) -> str | None:
    """Attempt to scrape the user's full name from the LinkedIn profile/feed page."""
    try:
        # Navigate to home or me
        if not _is_feed_or_group_url(page.url):
             page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=15000)
        
        # 1. Try left rail name selector
        name_selector = ".t-16.t-black.t-bold"
        try:
            page.wait_for_selector(name_selector, timeout=5000)
            name = page.locator(name_selector).first.inner_text().strip()
            if name:
                logger.info("Scraped user name: %s", name)
                return name
        except Exception:
            pass
            
        # 2. Try Global Nav image alt attribute
        img_selector = "img.global-nav__me-photo"
        if page.locator(img_selector).count() > 0:
            alt = page.locator(img_selector).first.get_attribute("alt")
            if alt:
                # "Photo of Viet PQ" or "Ảnh của Viet PQ"
                clean_name = alt.replace("Photo of ", "").replace("Ảnh của ", "").strip()
                logger.info("Scraped user name from alt: %s", clean_name)
                return clean_name
                
        return None
    except Exception as e:
        logger.warning("Failed to scrape user full name: %s", e)
        return None


_auth_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="linkedin_auth_playwright")

def login_and_save_session(
    email: str,
    password: str,
    session_id: str | None = None,
    force_relogin: bool = True,
) -> LoginFlowResult:
    future = _auth_executor.submit(
        _login_and_save_session_sync,
        email,
        password,
        session_id,
        force_relogin,
    )
    return future.result()


def verify_pending_login_otp(
    pending_session_id: str,
    otp_code: str,
    checkpoint_url: str | None = None,
) -> tuple[str, Path, str]:
    future = _auth_executor.submit(
        _verify_pending_login_otp_sync,
        pending_session_id,
        otp_code,
        checkpoint_url,
    )
    return future.result()

