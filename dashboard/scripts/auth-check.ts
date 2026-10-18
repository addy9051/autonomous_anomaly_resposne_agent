import assert from "node:assert/strict";
import { SignJWT, generateKeyPair } from "jose";
import { verifyAlbToken } from "../lib/server/auth";
import { isAllowed } from "../lib/server/policy";
import { sessionName } from "../lib/server/signer";

const ARN = "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/dash/abc";

async function main() {
  const { privateKey, publicKey } = await generateKeyPair("ES256");
  const other = await generateKeyPair("ES256");
  const getKey = async () => publicKey;
  const sign = (key: CryptoKey, signer = ARN) =>
    new SignJWT({ email: "a@x.com", groups: "sre" })
      .setProtectedHeader({ alg: "ES256", kid: "kid-12345678", signer })
      .setExpirationTime("5m")
      .sign(key);

  const ok = await verifyAlbToken(await sign(privateKey), { region: "us-east-1", albArn: ARN, getKey });
  assert.equal(ok.email, "a@x.com");

  await assert.rejects(verifyAlbToken(await sign(privateKey, "arn:other"), { region: "r", albArn: ARN, getKey }), /expected load balancer/);
  await assert.rejects(verifyAlbToken(await sign(other.privateKey), { region: "r", albArn: ARN, getKey }));
  await assert.rejects(verifyAlbToken("not.a.jwt", { region: "r", albArn: ARN, getKey }));
  const expired = await new SignJWT({})
    .setProtectedHeader({ alg: "ES256", kid: "kid-12345678", signer: ARN })
    .setExpirationTime("-1m")
    .sign(privateKey);
  await assert.rejects(verifyAlbToken(expired, { region: "r", albArn: ARN, getKey }));

  assert.ok(isAllowed("sre", "POST", "/api/v1/incidents/abc/approve", false));
  assert.ok(!isAllowed("pm", "POST", "/api/v1/incidents/abc/approve", false));
  assert.ok(!isAllowed("pm", "PUT", "/api/v1/agent/controls", false));
  assert.ok(isAllowed("pm", "POST", "/api/v1/incidents/abc/feedback", false));
  assert.ok(isAllowed("pm", "GET", "/api/v1/metrics/performance", false));
  assert.ok(!isAllowed("sre", "POST", "/api/v1/events/process", false));
  assert.ok(isAllowed("sre", "POST", "/api/v1/events/process", true));
  assert.ok(!isAllowed("sre", "GET", "/api/v1/incidents/a/b/c", false));
  assert.ok(!isAllowed("sre", "GET", "/api/v1/knowledge/search", false));
  assert.match(sessionName("a b/c@x.com"), /^[\w+=,.@-]{2,64}$/);
  console.log("all checks passed");
}

void main();
