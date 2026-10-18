/**
 * Dashboard API Client
 * Centralized logic for interacting with the Anomaly Response Agent FastAPI.
 */

// Calls go to the dashboard's own server-side proxy, which authenticates the user and signs
// the request. No credential ever reaches the browser.
const API_BASE = "/api/backend/api/v1";

export interface Capabilities {
  approve: boolean;
  control: boolean;
  manageConnectors: boolean;
  demo: boolean;
}

export interface DashboardSession {
  user: string;
  role: "sre" | "pm";
  capabilities: Capabilities;
}

export async function fetchSession(): Promise<DashboardSession> {
  const res = await fetch("/api/session", { cache: "no-store" });
  if (!res.ok) throw new Error(`Session unavailable (${res.status})`);
  return res.json();
}

export interface TelemetryEvent {
  event_id: string;
  timestamp: string;
  source: string;
  service_name: string;
  event_type: string;
  payload: Record<string, any>;
}

export interface IncidentRecord {
  incident_id: string;
  status: string;
  created_at: string;
  updated_at?: string;
  resolved_at?: string | null;
  anomaly_event?: {
    severity?: string;
    anomaly_type?: string;
    affected_services?: string[];
    confidence?: number;
    timestamp?: string;
    metrics_snapshot?: Record<string, number | null>;
  } | null;
  diagnosis_result?: {
    root_cause?: string;
    root_cause_category?: string;
    decision_summary?: string;
    reasoning_chain?: string;
    confidence?: number;
    confidence_band?: string;
    hypotheses?: Array<{
      hypothesis: string;
      confidence_band: string;
      affected_scope?: string[];
      supporting_evidence?: Array<{ source: string; description?: string; value?: unknown; unit?: string }>;
      contradicting_evidence?: Array<{ source: string; description?: string; value?: unknown; unit?: string }>;
      missing_evidence?: string[];
    }>;
    sub_agent_reports?: Record<string, { findings: string; confidence: number }>;
    evidence_timeline?: Array<{
      source: string;
      description?: string;
      value?: unknown;
      unit?: string;
      observed_at?: string;
    }>;
    recommended_actions?: Array<{
      action: string;
      estimated_impact?: string;
      rollback_steps?: string[];
    }>;
  } | null;
  action_results: Array<{
    action_taken: string;
    execution_status: string;
    output?: Record<string, unknown>;
    error?: string | null;
  }>;
  verification_result?: {
    outcome: string;
    details?: string;
    checked_signals?: Record<string, unknown>;
  } | null;
  auto_resolved?: boolean;
  human_overrode?: boolean;
  human_feedback?: string | null;
  false_positive?: boolean;
  regressed?: boolean | null;
  time_to_detect_seconds?: number | null;
  time_to_mitigate_seconds?: number | null;
  total_llm_cost_usd: number;
  total_llm_tokens_used?: number;
}

export interface PerformanceMetrics {
  window: "24h" | "7d" | "30d";
  generated_at: string;
  totals: {
    incidents: number;
    recovered: number;
    auto_resolved: number;
    human_overrides: number;
    false_positives: number;
  };
  rates: {
    resolution: number | null;
    autonomous_resolution: number | null;
    human_override: number | null;
    false_positive: number | null;
  };
  timings_seconds: {
    mean_time_to_detect: number | null;
    mean_time_to_mitigate: number | null;
  };
  cost: {
    total_usd: number;
    per_incident_usd: number | null;
    total_tokens: number;
    tokens_per_incident: number | null;
  };
  funnel: {
    detected: number;
    diagnosed: number;
    with_recommendations: number;
    with_actions_recorded: number;
    verified_recovered: number;
  };
}

export interface AgentControls {
  paused: boolean;
  service_autonomy: Record<string, "observe" | "suggest" | "approve_to_act" | "autonomous">;
}

export interface AuditEntry {
  id: number;
  incident_id: string;
  timestamp: string;
  actor: string;
  action: string;
  details: Record<string, unknown>;
}

export interface IncidentStreamSnapshot {
  active_incidents: IncidentRecord[];
  recent_incidents: IncidentRecord[];
  generated_at: string;
}

export interface InferenceReadiness {
  chat: "ready" | "starting" | "unavailable";
  embeddings: "ready" | "starting" | "unavailable";
}

export interface DetailedHealth {
  status: string;
  inference?: InferenceReadiness;
}

/**
 * Enhanced fetcher with logging
 */
async function fetchWithLogs(url: string, options: RequestInit = {}) {
  try {
    const res = await fetch(url, options);
    if (!res.ok) {
      const errorText = await res.text();
      console.error(`API Error [${res.status}] at ${url}:`, errorText);
      throw new Error(`API Error ${res.status}: ${errorText}`);
    }
    return res.json();
  } catch (err) {
    console.error(`Fetch failure at ${url}:`, err);
    throw err;
  }
}

export async function fetchStats() {
  return fetchWithLogs(`${API_BASE}/status`, { cache: 'no-store' });
}

export async function fetchPerformanceMetrics(window: PerformanceMetrics["window"] = "7d"): Promise<PerformanceMetrics> {
  return fetchWithLogs(`${API_BASE}/metrics/performance?window=${window}`, { cache: "no-store" });
}

export async function fetchAgentControls(): Promise<AgentControls> {
  return fetchWithLogs(`${API_BASE}/agent/controls`, { cache: "no-store" });
}

export async function saveAgentControls(
  patch: { paused?: boolean } | { service: string; autonomy: AgentControls["service_autonomy"][string] | null },
): Promise<AgentControls> {
  return fetchWithLogs(`${API_BASE}/agent/controls`, jsonRequest("PUT", patch));
}

export async function fetchAuditEntries(limit = 100): Promise<AuditEntry[]> {
  const data = await fetchWithLogs(`${API_BASE}/audit?limit=${limit}`, { cache: "no-store" });
  return data.entries;
}

export async function streamIncidentUpdates(
  signal: AbortSignal,
  onSnapshot: (snapshot: IncidentStreamSnapshot) => void,
): Promise<void> {
  const response = await fetch(`${API_BASE}/events/stream`, {
    cache: "no-store",
    signal,
  });
  if (!response.ok || !response.body) {
    const body = await response.text();
    throw new Error(`Incident stream failed (${response.status}): ${body}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() ?? "";
    for (const block of blocks) {
      const data = block
        .split(/\r?\n/)
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n");
      if (data) onSnapshot(JSON.parse(data) as IncidentStreamSnapshot);
    }
  }
}

export async function fetchRecentTelemetry(): Promise<TelemetryEvent[]> {
  const data = await fetchWithLogs(`${API_BASE}/telemetry/recent`, { cache: 'no-store' });
  return data.events || [];
}

export async function fetchActiveIncidents(): Promise<IncidentRecord[]> {
  const data = await fetchWithLogs(`${API_BASE}/incidents/active`, { cache: 'no-store' });
  return data.incidents || [];
}

export async function fetchResolvedIncidents(limit = 10): Promise<IncidentRecord[]> {
  const data = await fetchWithLogs(`${API_BASE}/incidents?limit=${limit}`, { cache: 'no-store' });
  return data.incidents || [];
}

export async function fetchDetailedHealth(): Promise<DetailedHealth> {
  return fetchWithLogs(`${API_BASE}/health/detailed`, { cache: 'no-store' });
}

export interface ConnectorField {
  key: string;
  label: string;
  kind: "text" | "password" | "number" | "boolean";
  secret: boolean;
  required: boolean;
  placeholder: string;
  help: string;
  value?: string | number | boolean;
  is_set?: boolean;
}

export interface ConnectorInfo {
  id: string;
  label: string;
  description: string;
  selected: boolean;
  configured: boolean;
  missing: string[];
  fields: ConnectorField[];
}

export interface VendorStatus {
  last_success_age_seconds: number | null;
  last_error: string | null;
  consecutive_failures: number;
  records_ingested: number;
}

export interface ConnectorsView {
  connectors: ConnectorInfo[];
  selected: string[];
  connector_modes: string;
  ingestion: { enabled: boolean; services: string; dry_run: boolean; poll_interval_seconds: number };
  active: boolean;
  status?: { services: string[]; escalations: number; vendors: Record<string, VendorStatus> };
}

function jsonRequest(method: string, body?: unknown): RequestInit {
  return {
    method,
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  };
}

export async function fetchConnectors(): Promise<ConnectorsView> {
  return fetchWithLogs(`${API_BASE}/connectors`, jsonRequest("GET"));
}

export async function saveConnectorSelection(connectors: string[]): Promise<ConnectorsView> {
  return fetchWithLogs(`${API_BASE}/connectors/selection`, jsonRequest("PUT", { connectors }));
}

export async function saveConnectorCredentials(
  vendor: string,
  values: Record<string, unknown>,
): Promise<ConnectorsView> {
  return fetchWithLogs(`${API_BASE}/connectors/${vendor}/credentials`, jsonRequest("PUT", { values }));
}

export async function testConnector(
  vendor: string,
  values: Record<string, unknown>,
): Promise<{ ok: boolean; detail: string }> {
  return fetchWithLogs(`${API_BASE}/connectors/${vendor}/test`, jsonRequest("POST", { values }));
}

export async function activateIngestion(body: {
  services: string;
  dry_run: boolean;
  poll_interval_seconds: number;
}): Promise<ConnectorsView> {
  return fetchWithLogs(`${API_BASE}/connectors/activate`, jsonRequest("POST", body));
}

export async function deactivateIngestion(): Promise<ConnectorsView> {
  return fetchWithLogs(`${API_BASE}/connectors/deactivate`, jsonRequest("POST"));
}

export async function approveAction(incidentId: string, approved: boolean, reason?: string) {
  return fetchWithLogs(`${API_BASE}/incidents/${incidentId}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approved, reason }),
  });
}

export async function submitIncidentFeedback(
  incidentId: string,
  feedback: {
    verdict: "helpful" | "unhelpful";
    category?: "wrong_root_cause" | "unsafe_fix" | "too_slow" | "missed_context" | "other";
    comment?: string;
  },
) {
  return fetchWithLogs(`${API_BASE}/incidents/${encodeURIComponent(incidentId)}/feedback`, jsonRequest("POST", feedback));
}

export async function triggerDemo() {
  return fetchWithLogs(`${API_BASE}/events/process`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      source: "payment_gateway",
      service_name: "payment-gateway",
      event_type: "metric",
      payload: {
        timestamp: new Date().toISOString(),
        cpu_percent: 85.5,
        memory_percent: 92.1,
        p99_latency_ms: 1500.0,
        error_rate: 0.15
      }
    }),
  });
}
