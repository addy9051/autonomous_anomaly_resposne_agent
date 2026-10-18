/**
 * Dashboard API Client
 * Centralized logic for interacting with the Anomaly Response Agent FastAPI.
 */

// Use localhost consistently to match the browser's address bar and resolve CORS issues on Windows
const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8010/api/v1";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY;

function authHeaders(): Record<string, string> {
  return API_KEY ? { "X-API-Key": API_KEY } : {};
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
  anomaly_event?: any;
  diagnosis_result?: any;
  action_results: any[];
  verification_result?: any;
  total_llm_cost_usd: number;
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
    const res = await fetch(url, {
      ...options,
      headers: { ...authHeaders(), ...(options.headers as Record<string, string> | undefined) },
    });
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
    headers: { "Content-Type": "application/json", ...authHeaders() },
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

export async function approveAction(incidentId: string, approved: boolean) {
  return fetchWithLogs(`${API_BASE}/incidents/${incidentId}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approved }),
  });
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
