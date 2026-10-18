import { Sha256 } from "@aws-crypto/sha256-js";
import { AssumeRoleCommand, STSClient } from "@aws-sdk/client-sts";
import { HttpRequest } from "@smithy/protocol-http";
import { SignatureV4 } from "@smithy/signature-v4";
import type { Principal } from "./auth";

interface Credentials {
  accessKeyId: string;
  secretAccessKey: string;
  sessionToken: string;
  expiration: Date;
}

const REFRESH_MARGIN_MS = 5 * 60 * 1000;
const MAX_CACHED_SESSIONS = 500;

const sessions = new Map<string, Credentials>();
let sts: STSClient | undefined;

/** Role session names become the tail of the caller ARN the API records as the actor. */
export function sessionName(user: string): string {
  return user.replace(/[^\w+=,.@-]/g, "_").slice(0, 64).padEnd(2, "_");
}

function roleArn(principal: Principal): string {
  const arn = principal.role === "sre" ? process.env.DASHBOARD_SRE_ROLE_ARN : process.env.DASHBOARD_PM_ROLE_ARN;
  if (!arn) throw new Error(`No IAM role configured for dashboard role '${principal.role}'.`);
  return arn;
}

async function credentialsFor(principal: Principal): Promise<Credentials> {
  const key = `${principal.role}:${principal.user}`;
  const cached = sessions.get(key);
  if (cached && cached.expiration.getTime() - Date.now() > REFRESH_MARGIN_MS) return cached;

  sts ??= new STSClient({ region: process.env.AWS_REGION });
  const result = await sts.send(
    new AssumeRoleCommand({
      RoleArn: roleArn(principal),
      RoleSessionName: sessionName(principal.user),
      DurationSeconds: 3600,
    }),
  );
  const c = result.Credentials;
  if (!c?.AccessKeyId || !c.SecretAccessKey || !c.SessionToken || !c.Expiration) {
    throw new Error("STS returned incomplete credentials.");
  }
  if (sessions.size >= MAX_CACHED_SESSIONS) sessions.delete(sessions.keys().next().value as string);
  const credentials = {
    accessKeyId: c.AccessKeyId,
    secretAccessKey: c.SecretAccessKey,
    sessionToken: c.SessionToken,
    expiration: c.Expiration,
  };
  sessions.set(key, credentials);
  return credentials;
}

/** Build SigV4 headers for a private API Gateway call made as the signed-in user. */
export async function signRequest(
  principal: Principal,
  url: URL,
  method: string,
  headers: Record<string, string>,
  body?: Uint8Array,
): Promise<Record<string, string>> {
  const credentials = await credentialsFor(principal);
  const signer = new SignatureV4({
    service: "execute-api",
    region: process.env.AWS_REGION as string,
    credentials,
    sha256: Sha256,
  });
  const query: Record<string, string> = {};
  url.searchParams.forEach((value, name) => {
    query[name] = value;
  });
  const signed = await signer.sign(
    new HttpRequest({
      method,
      protocol: url.protocol,
      hostname: url.hostname,
      port: url.port ? Number(url.port) : undefined,
      path: url.pathname,
      query,
      headers: { ...headers, host: url.host },
      body,
    }),
  );
  const { host: _host, ...rest } = signed.headers;
  return rest;
}
