/**
 * Route allow-lists per dashboard role. These mirror the IAM invoke policies in
 * infra/aws/terraform (caller_route_access) so a request is rejected here before it is
 * ever signed, and IAM remains the authoritative second layer.
 */

export type Role = "sre" | "pm";

type Rule = readonly [method: string, pattern: RegExp];

const SEGMENT = "[^/]+";

const READ_ONLY: Rule[] = [
  ["GET", /^\/api\/v1\/incidents$/],
  ["GET", /^\/api\/v1\/incidents\/active$/],
  ["GET", new RegExp(`^/api/v1/incidents/${SEGMENT}$`)],
  ["GET", new RegExp(`^/api/v1/incidents/${SEGMENT}/brief$`)],
  ["GET", new RegExp(`^/api/v1/episodes/${SEGMENT}$`)],
  ["GET", /^\/api\/v1\/telemetry\/recent$/],
  ["GET", /^\/api\/v1\/metrics\/performance$/],
  ["GET", /^\/api\/v1\/audit$/],
  ["GET", /^\/api\/v1\/events\/stream$/],
  ["GET", /^\/api\/v1\/agent\/controls$/],
  ["GET", /^\/api\/v1\/actions\/tiers$/],
  ["GET", /^\/api\/v1\/status$/],
  ["GET", /^\/api\/v1\/health\/detailed$/],
];

const FEEDBACK: Rule = ["POST", new RegExp(`^/api/v1/incidents/${SEGMENT}/feedback$`)];

const RULES: Record<Role, Rule[]> = {
  pm: [...READ_ONLY, FEEDBACK],
  sre: [
    ...READ_ONLY,
    FEEDBACK,
    ["POST", new RegExp(`^/api/v1/incidents/${SEGMENT}/approve$`)],
    ["PUT", /^\/api\/v1\/agent\/controls$/],
    ["GET", /^\/api\/v1\/connectors$/],
    ["PUT", /^\/api\/v1\/connectors\/selection$/],
    ["PUT", new RegExp(`^/api/v1/connectors/${SEGMENT}/credentials$`)],
    ["POST", new RegExp(`^/api/v1/connectors/${SEGMENT}/test$`)],
    ["POST", /^\/api\/v1\/connectors\/activate$/],
    ["POST", /^\/api\/v1\/connectors\/deactivate$/],
    ["GET", /^\/api\/v1\/ingestion\/status$/],
  ],
};

// Only dev mode may drive the synthetic anomaly generator.
const DEV_ONLY: Rule[] = [["POST", /^\/api\/v1\/events\/process$/]];

export function isAllowed(role: Role, method: string, path: string, devMode: boolean): boolean {
  const rules = devMode ? [...RULES[role], ...DEV_ONLY] : RULES[role];
  return rules.some(([m, pattern]) => m === method && pattern.test(path));
}

export interface Capabilities {
  approve: boolean;
  control: boolean;
  manageConnectors: boolean;
  demo: boolean;
}

export function capabilitiesFor(role: Role, devMode: boolean): Capabilities {
  const sre = role === "sre";
  return { approve: sre, control: sre, manageConnectors: sre, demo: devMode };
}
