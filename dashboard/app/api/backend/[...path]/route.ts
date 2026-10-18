import { NextRequest, NextResponse } from "next/server";
import { authenticate, authMode, AuthError, type Principal } from "@/lib/server/auth";
import { isAllowed } from "@/lib/server/policy";
import { signRequest } from "@/lib/server/signer";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const MAX_BODY_BYTES = 1024 * 1024;
const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

function problem(status: number, detail: string): NextResponse {
  return NextResponse.json({ detail }, { status, headers: { "Cache-Control": "no-store" } });
}

function sameOrigin(request: NextRequest): boolean {
  const origin = request.headers.get("origin");
  if (!origin) return false;
  const host = request.headers.get("x-forwarded-host") ?? request.headers.get("host");
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

function cleanSegments(segments: string[]): string[] | null {
  const safe = segments.every((s) => s !== "" && s !== "." && s !== ".." && !/[\\/\u0000-\u001f]/.test(s));
  return safe ? segments : null;
}

async function handle(request: NextRequest, context: { params: Promise<{ path: string[] }> }): Promise<NextResponse> {
  const segments = cleanSegments((await context.params).path ?? []);
  if (!segments) return problem(400, "Invalid path.");
  const path = `/${segments.join("/")}`;
  const method = request.method;

  if (method !== "GET" && !sameOrigin(request)) return problem(403, "Cross-origin request rejected.");

  let principal: Principal;
  try {
    principal = await authenticate(request.headers);
  } catch (error) {
    if (error instanceof AuthError) return problem(error.status, error.message);
    console.error("dashboard_auth_failure", error);
    return problem(500, "Authentication failed.");
  }

  const dev = authMode() === "dev";
  if (!isAllowed(principal.role, method, path, dev)) {
    return problem(403, "Your role is not permitted to perform this action.");
  }

  const base = process.env.DASHBOARD_API_URL;
  if (!base) return problem(500, "Dashboard API URL is not configured.");
  const target = new URL(`${base.replace(/\/+$/, "")}${path}${request.nextUrl.search}`);
  if (dev && !LOOPBACK_HOSTS.has(target.hostname)) {
    return problem(500, "Dev authentication mode may only target a loopback API.");
  }

  const body = method === "GET" ? undefined : new Uint8Array(await request.arrayBuffer());
  if (body && body.byteLength > MAX_BODY_BYTES) return problem(413, "Request body too large.");

  const outgoing: Record<string, string> = { accept: request.headers.get("accept") ?? "application/json" };
  if (body) outgoing["content-type"] = "application/json";

  try {
    const headers = dev
      ? { ...outgoing, ...(process.env.API_AUTH_TOKEN ? { "x-api-key": process.env.API_AUTH_TOKEN } : {}) }
      : { ...outgoing, ...(await signRequest(principal, target, method, outgoing, body)) };

    const upstream = await fetch(target, {
      method,
      headers,
      body: body as BodyInit | undefined,
      signal: request.signal,
      cache: "no-store",
    });

    return new NextResponse(upstream.body, {
      status: upstream.status,
      headers: {
        "Content-Type": upstream.headers.get("content-type") ?? "application/json",
        "Cache-Control": "no-store",
        "X-Accel-Buffering": "no",
      },
    });
  } catch (error) {
    if (request.signal.aborted) return new NextResponse(null, { status: 499 });
    console.error("dashboard_upstream_failure", { path, user: principal.user, error });
    return problem(502, "The agent API is unreachable.");
  }
}

export { handle as GET, handle as POST, handle as PUT };
