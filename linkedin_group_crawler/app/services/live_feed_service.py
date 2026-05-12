from __future__ import annotations
import sqlite3
import threading
import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)

# Database configuration
DB_PATH = Path(__file__).parent.parent.parent / "data" / "live_feed.db"
MAX_EVENTS = 1000
MAX_AGE_DAYS = 30

# Valid event types
VALID_EVENT_TYPES = {
    "crawl_start",
    "crawl_complete",
    "report",
    "task_add",
    "seeding_report",
    "verify_success",
    "verify_fail",
}

_db_lock = threading.Lock()
_cleanup_timer: threading.Timer | None = None

def _init_db():
    """Initialize SQLite database for live feed."""
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS live_feed_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER UNIQUE,
                event_type TEXT NOT NULL,
                user TEXT NOT NULL,
                message TEXT,
                timestamp TEXT NOT NULL,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON live_feed_events(timestamp DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_user ON live_feed_events(user)")
        conn.commit()
        conn.close()
        logger.info("Live feed database initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize live feed database: {e}")

def _cleanup_old_events_sync():
    """Internal function: Remove events older than MAX_AGE_DAYS or keep only MAX_EVENTS."""
    if MAX_EVENTS <= 0:
        logger.warning(f"Invalid MAX_EVENTS config: {MAX_EVENTS}. Skipping cleanup.")
        return
    if MAX_AGE_DAYS <= 0:
        logger.warning(f"Invalid MAX_AGE_DAYS config: {MAX_AGE_DAYS}. Skipping cleanup.")
        return
    
    try:
        with _db_lock:
            conn = None
            try:
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                
                # Remove by age
                cutoff_date = (datetime.now() - timedelta(days=MAX_AGE_DAYS)).isoformat()
                cursor.execute("DELETE FROM live_feed_events WHERE timestamp < ?", (cutoff_date,))
                deleted_by_age = cursor.rowcount
                
                # Remove excess events (keep only MAX_EVENTS)
                cursor.execute("""
                    DELETE FROM live_feed_events 
                    WHERE id NOT IN (
                        SELECT id FROM live_feed_events 
                        ORDER BY timestamp DESC 
                        LIMIT ?
                    )
                """, (MAX_EVENTS,))
                deleted_by_count = cursor.rowcount
                
                conn.commit()
                
                if deleted_by_age > 0 or deleted_by_count > 0:
                    logger.debug(f"Cleaned up live feed: {deleted_by_age} by age, {deleted_by_count} by count")
            finally:
                if conn:
                    conn.close()
    except Exception as e:
        logger.error(f"Failed to cleanup old events: {e}")

def _schedule_cleanup():
    """Schedule cleanup to run in background after 1 hour."""
    global _cleanup_timer
    # Prevent duplicate timers if module is reloaded - only start if not already running
    if _cleanup_timer is not None and _cleanup_timer.is_alive():
        return  # Timer already scheduled
    if _cleanup_timer is not None:
        _cleanup_timer.cancel()
    _cleanup_timer = threading.Timer(3600, _cleanup_old_events_sync)
    _cleanup_timer.daemon = True
    _cleanup_timer.start()
    logger.debug("Cleanup timer scheduled for 3600 seconds")

def add_event(event_type: str, user: str, message: str, metadata: Dict[str, Any] = None) -> Dict[str, Any] | None:
    """Adds a new event to the live feed (stored in SQLite).
    
    Args:
        event_type: Type of event (must be in VALID_EVENT_TYPES)
        user: User who performed the action
        message: Description of the action
        metadata: Additional metadata (dict)
    
    Returns:
        Event dict or None if validation fails
    """
    # Validate event_type
    if event_type not in VALID_EVENT_TYPES:
        logger.warning(f"Invalid event_type: {event_type}. Valid types: {VALID_EVENT_TYPES}")
        return None
    
    # Validate user
    if not user or not isinstance(user, str) or len(user.strip()) == 0:
        logger.warning(f"Invalid user: {user}")
        return None
    
    event_id = int(datetime.now().timestamp() * 1000)
    timestamp = datetime.now().isoformat()
    metadata_json = json.dumps(metadata or {})
    
    event = {
        "id": event_id,
        "type": event_type,
        "user": user.strip(),
        "message": message or "",
        "timestamp": timestamp,
        "metadata": metadata or {}
    }
    
    try:
        with _db_lock:
            conn = None
            try:
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO live_feed_events (event_id, event_type, user, message, timestamp, metadata)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (event_id, event_type, user.strip(), message or "", timestamp, metadata_json))
                conn.commit()
                logger.debug(f"Added event: {event_type} from {user}")
            except sqlite3.IntegrityError:
                logger.debug(f"Duplicate event_id {event_id}, skipped")
            finally:
                if conn:
                    conn.close()
    except Exception as e:
        logger.error(f"Failed to add event: {e}")
        return None
    
    return event

def get_feed(limit: int = 50) -> List[Dict[str, Any]]:
    """Returns the latest live feed events from database.
    
    Args:
        limit: Maximum number of events to return (default 50)
    
    Returns:
        List of event dicts
    """
    try:
        with _db_lock:
            conn = None
            try:
                conn = sqlite3.connect(DB_PATH)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT event_id, event_type, user, message, timestamp, metadata
                    FROM live_feed_events
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (limit,))
                
                rows = cursor.fetchall()
            finally:
                if conn:
                    conn.close()
        
        events = []
        for row in rows:
            try:
                events.append({
                    "id": row["event_id"],
                    "type": row["event_type"],
                    "user": row["user"],
                    "message": row["message"],
                    "timestamp": row["timestamp"],
                    "metadata": json.loads(row["metadata"]) if row["metadata"] else {}
                })
            except Exception as e:
                logger.error(f"Failed to parse event row: {e}")
                continue
        
        return events
    except Exception as e:
        logger.error(f"Failed to get feed: {e}")
        return []

def get_user_activities(user: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Get activities for a specific user.
    
    Args:
        user: User email/name to filter by
        limit: Maximum number of events to return
    
    Returns:
        List of event dicts for the user
    """
    try:
        with _db_lock:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT event_id, event_type, user, message, timestamp, metadata
                FROM live_feed_events
                WHERE user = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (user, limit))
            
            rows = cursor.fetchall()
            conn.close()
        
        events = []
        for row in rows:
            try:
                events.append({
                    "id": row["event_id"],
                    "type": row["event_type"],
                    "user": row["user"],
                    "message": row["message"],
                    "timestamp": row["timestamp"],
                    "metadata": json.loads(row["metadata"]) if row["metadata"] else {}
                })
            except Exception as e:
                logger.error(f"Failed to parse event row: {e}")
                continue
        
        return events
    except Exception as e:
        logger.error(f"Failed to get user activities: {e}")
        return []

# Initialize database on import
_init_db()
# Schedule cleanup to run in background (not blocking add_event)
_schedule_cleanup()
