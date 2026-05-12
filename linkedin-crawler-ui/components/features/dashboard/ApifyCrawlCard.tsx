"use client";

import { useState } from "react";

import { MaterialIcon } from "@/components/ui";
import { apifyCrawlGroup } from "@/services/linkedinCrawlerService";

type ApifyResult = {
  success: boolean;
  message: string;
  likes?: number;
  comments?: number;
  reposts?: number;
  content?: string;
  post_url?: string;
};

/**
 * Option B: Crawl thủ công 1 nhóm LinkedIn bằng Apify API.
 * Không cần session/email — chỉ cần Group URL và APIFY_TOKEN đã cấu hình.
 */
export function ApifyCrawlCard() {
  const [groupUrl, setGroupUrl] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [result, setResult] = useState<ApifyResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleApifyCrawl = async () => {
    const url = groupUrl.trim();
    if (!url) return;

    setIsLoading(true);
    setResult(null);
    setError(null);

    try {
      const res = await apifyCrawlGroup(url);
      if (res.success && res.data) {
        const top = res.data.top_post;
        setResult({
          success: true,
          message: res.message,
          likes: top?.likes ?? 0,
          comments: top?.comments ?? 0,
          reposts: top?.reposts ?? 0,
          content: top?.content ?? "",
          post_url: top?.post_url ?? "",
        });
      } else {
        setResult({
          success: false,
          message: res.message || "Không có bài trong 24h hoặc lỗi Apify.",
        });
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Lỗi không xác định.");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <section className="flex flex-col gap-md">
      <div className="border-outline-variant bg-surface-container-lowest flex flex-col gap-md rounded-xl border p-lg shadow-sm">
        {/* Header */}
        <div className="border-surface-variant mb-sm flex items-center gap-2 border-b pb-md">
          <MaterialIcon name="bolt" className="shrink-0 text-warning" />
          <div>
            <h2 className="text-h3 font-semibold">Crawl bằng Apify API</h2>
            <p className="text-body-sm text-on-surface-variant mt-xs">
              Phương án dự phòng — không cần đăng nhập LinkedIn. Apify sẽ thu
              thập bài viết qua API cloud.
            </p>
          </div>
        </div>

        {/* Apify info badge */}
        <div className="bg-warning/10 border-warning/30 flex items-start gap-2 rounded-lg border px-md py-sm">
          <MaterialIcon
            name="info"
            className="text-warning mt-0.5 shrink-0 text-sm"
          />
          <p className="text-body-sm text-on-surface-variant">
            <span className="text-on-surface font-semibold">Option B:</span>{" "}
            Crawl thủ công qua Apify API. Chọn cách này khi Playwright bị
            block. Thời gian xử lý 2–8 phút tuỳ Actor.
          </p>
        </div>

        {/* Input */}
        <div className="flex flex-col gap-base">
          <label
            htmlFor="apify-group-url"
            className="text-label-md text-on-surface-variant font-semibold tracking-wide uppercase"
          >
            URL nhóm LinkedIn
          </label>
          <div className="flex gap-sm">
            <input
              id="apify-group-url"
              type="url"
              className="border-outline-variant bg-surface focus:border-primary focus:ring-primary min-w-0 flex-1 rounded-lg border px-md py-sm font-mono text-sm transition-all outline-none focus:ring-1"
              placeholder="https://www.linkedin.com/groups/1234567/"
              value={groupUrl}
              onChange={(e) => setGroupUrl(e.target.value)}
              disabled={isLoading}
            />
            <button
              type="button"
              id="apify-crawl-btn"
              className="bg-warning text-on-warning hover:bg-warning/80 active:scale-[0.98] flex shrink-0 items-center gap-2 rounded-lg px-lg py-sm font-bold transition-all disabled:cursor-not-allowed disabled:opacity-60"
              onClick={() => void handleApifyCrawl()}
              disabled={isLoading || !groupUrl.trim()}
            >
              {isLoading ? (
                <>
                  <span className="border-on-warning size-4 animate-spin rounded-full border-2 border-t-transparent" />
                  Đang crawl...
                </>
              ) : (
                <>
                  <MaterialIcon name="bolt" className="text-sm" />
                  Crawl Apify
                </>
              )}
            </button>
          </div>
        </div>

        {/* Loading state */}
        {isLoading && (
          <div className="border-outline-variant bg-surface-container flex items-center gap-3 rounded-lg border px-md py-sm">
            <span className="border-primary size-5 animate-spin rounded-full border-2 border-t-transparent" />
            <div>
              <p className="text-body-sm text-on-surface font-medium">
                Apify Actor đang chạy...
              </p>
              <p className="text-body-sm text-on-surface-variant">
                Thường mất 2–8 phút. Hệ thống đang poll kết quả mỗi 10 giây.
              </p>
            </div>
          </div>
        )}

        {/* Error */}
        {error && (
          <div
            className="border-error-container bg-error-container/40 text-error flex items-start gap-2 rounded-lg border px-md py-sm text-sm"
            role="alert"
          >
            <MaterialIcon name="error" className="mt-0.5 shrink-0 text-sm" />
            {error}
          </div>
        )}

        {/* Result */}
        {result && !isLoading && (
          <div
            className={`rounded-lg border px-md py-sm ${
              result.success
                ? "border-secondary-container bg-secondary-container/20"
                : "border-error-container bg-error-container/20"
            }`}
            role={result.success ? "status" : "alert"}
          >
            {result.success ? (
              <div className="flex flex-col gap-sm">
                {/* Method badge */}
                <div className="flex items-center gap-2">
                  <span className="bg-warning/20 text-warning inline-flex items-center gap-1 rounded-full px-sm py-xs text-xs font-bold">
                    <MaterialIcon name="bolt" className="text-xs" />
                    Apify API
                  </span>
                  <span className="text-body-sm text-on-surface-variant">
                    {result.message}
                  </span>
                </div>

                {/* Stats */}
                <div className="flex flex-wrap gap-md">
                  <div className="flex items-baseline gap-1">
                    <span className="text-on-surface text-xl font-bold tabular-nums">
                      {result.likes?.toLocaleString("vi-VN") ?? 0}
                    </span>
                    <span className="text-body-sm text-on-surface-variant">
                      likes
                    </span>
                  </div>
                  <div className="flex items-baseline gap-1">
                    <span className="text-on-surface font-semibold tabular-nums">
                      {result.comments?.toLocaleString("vi-VN") ?? 0}
                    </span>
                    <span className="text-body-sm text-on-surface-variant">
                      comments
                    </span>
                  </div>
                  <div className="flex items-baseline gap-1">
                    <span className="text-on-surface font-semibold tabular-nums">
                      {result.reposts?.toLocaleString("vi-VN") ?? 0}
                    </span>
                    <span className="text-body-sm text-on-surface-variant">
                      reposts
                    </span>
                  </div>
                </div>

                {/* Content preview */}
                {result.content && (
                  <p className="text-body-sm text-on-surface-variant line-clamp-4 leading-relaxed">
                    {result.content}
                  </p>
                )}

                {/* Link */}
                {result.post_url && (
                  <a
                    href={result.post_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-primary hover:text-primary/80 inline-flex items-center gap-1 text-sm font-medium transition-colors"
                  >
                    Xem bài gốc
                    <MaterialIcon name="open_in_new" className="text-sm" />
                  </a>
                )}
              </div>
            ) : (
              <div className="flex items-start gap-2">
                <MaterialIcon
                  name="warning"
                  className="text-error mt-0.5 shrink-0 text-sm"
                />
                <p className="text-body-sm text-error">{result.message}</p>
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
