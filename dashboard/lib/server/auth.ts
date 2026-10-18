import { importSPKI, jwtVerify, decodeProtectedHeader, type JWTPayload } from "jose";
import type { Role } from "./policy";

export type AuthMode = "alb-oidc" | "dev";

export interface Principal {
  user: string;
  role: Role;
}

export class AuthError extends Error {
  constructor(
    readonly status: 401 | 403 | 500,
    message: string,
  ) {
    super(message);
  }
}

export function authMode(): AuthMode {
  return process.env.DASHBOARD_AUTH_MODE === "dev" ? "dev" : "alb-oidc";
}

function csv(name: string): string[] {
  return (process.env[name] ?? "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
}

function resolveRole(groups: string[]): Role | null {
  if (groups.some((group) => csv("DASHBOARD_SRE_GROUPS").includes(group))) return "sre";
  if (groups.some((group) => csv("DASHBOARD_PM_GROUPS").includes(group))) return "pm";
  return null;
}

function claimAsList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String);
  if (typeof value === "string") return value.split(/[,\s]+/).filter(Boolean);
  return [];
}

const keyCache = new Map<string, CryptoKey>();

async function albPublicKey(kid: string, region: string): Promise<CryptoKey> {
  const cached = keyCache.get(kid);
  if (cached) return cached;
  // kid is attacker-controlled until verified, so it must not be able to alter the URL.
  if (!/^[0-9a-zA-Z-]{8,64}$/.test(kid)) throw new AuthError(401, "Invalid identity token.");
  const response = await fetch(`https://public-keys.auth.elb.${region}.amazonaws.com/${kid}`);
  if (!response.ok) throw new AuthError(401, "Unable to verify identity token.");
  const key = await importSPKI(await response.text(), "ES256");
  keyCache.set(kid, key);
  return key;
}

export interface VerifyOptions {
  region: string;
  albArn: string;
  getKey?: (kid: string) => Promise<CryptoKey>;
}

/** Verify the ES256 token an ALB OIDC listener adds to each authenticated request. */
export async function verifyAlbToken(token: string, options: VerifyOptions): Promise<JWTPayload> {
  try {
    const header = decodeProtectedHeader(token);
    if (header.alg !== "ES256" || typeof header.kid !== "string") {
      throw new AuthError(401, "Invalid identity token.");
    }
    // ALB pads its base64url segments; padding is stripped for parsing only.
    const key = await (options.getKey ?? ((kid) => albPublicKey(kid, options.region)))(header.kid);
    const { payload, protectedHeader } = await jwtVerify(token, key, { algorithms: ["ES256"] });
    if (protectedHeader.signer !== options.albArn) {
      throw new AuthError(401, "Identity token was not issued by the expected load balancer.");
    }
    return payload;
  } catch (error) {
    if (error instanceof AuthError) throw error;
    throw new AuthError(401, "Invalid identity token.");
  }
}

export async function authenticate(headers: Headers): Promise<Principal> {
  if (authMode() === "dev") {
    const role = process.env.DASHBOARD_DEV_ROLE === "pm" ? "pm" : "sre";
    return { user: "local-dev", role };
  }

  const albArn = process.env.DASHBOARD_ALB_ARN;
  const region = process.env.AWS_REGION;
  if (!albArn || !region) throw new AuthError(500, "Dashboard authentication is not configured.");

  const token = headers.get("x-amzn-oidc-data");
  if (!token) throw new AuthError(401, "Sign in required.");

  const payload = await verifyAlbToken(token, { region, albArn });
  const userClaim = process.env.DASHBOARD_USER_CLAIM || "email";
  const user = String(payload[userClaim] ?? payload.sub ?? "");
  if (!user) throw new AuthError(401, "Identity token has no user.");

  const role = resolveRole(claimAsList(payload[process.env.DASHBOARD_GROUPS_CLAIM || "groups"]));
  if (!role) throw new AuthError(403, "Your account is not assigned to an SRE or AI PM group.");
  return { user, role };
}
