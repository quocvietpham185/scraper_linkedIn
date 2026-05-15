/**
 * Dedicated API route handler for /verify (OTP) — same long timeout as login.
 */
export const maxDuration = 300;

const BACKEND_URL =
  process.env.LINKEDIN_CRAWLER_INTERNAL_API_URL ?? "http://127.0.0.1:8111";

export async function POST(request: Request) {
  const apiKey =
    process.env.NEXT_PUBLIC_LINKEDIN_CRAWLER_API_KEY ??
    process.env.LINKEDIN_CRAWLER_API_KEY ??
    "";

  try {
    const body = await request.text();

    const upstream = await fetch(`${BACKEND_URL}/verify`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(apiKey ? { "x-api-key": apiKey } : {}),
      },
      body,
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
