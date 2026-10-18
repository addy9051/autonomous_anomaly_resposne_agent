import { NextRequest, NextResponse } from "next/server";
import { authenticate, authMode, AuthError } from "@/lib/server/auth";
import { capabilitiesFor } from "@/lib/server/policy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest): Promise<NextResponse> {
  try {
    const principal = await authenticate(request.headers);
    const dev = authMode() === "dev";
    return NextResponse.json(
      { user: principal.user, role: principal.role, capabilities: capabilitiesFor(principal.role, dev) },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    if (error instanceof AuthError) {
      return NextResponse.json({ detail: error.message }, { status: error.status });
    }
    return NextResponse.json({ detail: "Authentication failed." }, { status: 500 });
  }
}
