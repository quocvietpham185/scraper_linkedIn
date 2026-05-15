/**
 * Dedicated API route handler for /login so we can set a long timeout.
 * Next.js rewrites have ~30s timeout; Playwright login can take 60-120s.
 */
export const maxDuration = 300; // 5 minutes

const BACKEND_URL =
  process.env.LINKEDIN_CRAWLER_INTERNAL_API_URL ?? "http://127.0.0.1:8111";

export async function POST(request: Request) {
  const apiKey =
    process.env.NEXT_PUBLIC_LINKEDIN_CRAWLER_API_KEY ??
    process.env.LINKEDIN_CRAWLER_API_KEY ??
    "";

  try {
    const body = await request.text();

    const upstream = await fetch(`${BACKEND_URL}/login`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(apiKey ? { "x-api-key": apiKey } : {}),
      },
      body,
      // Node.js fetch signal is forwarded from the client's AbortSignal
      signal: request.signal,
    });

    const responseBody = await upstream.text();

    return new Response(responseBody, {
      status: upstream.status,
      headers: {
        "Content-Type":
          upstream.headers.get("content-type") ?? "application/json",
      },
    });
  } catch (err) {
    const message =
      err instanceof Error ? err.message : "Unknown proxy error";
    return Response.json(
      { success: false, message: `Proxy error: ${message}` },
      { status: 502 },
    );
  }
}
