/**
 * Dashboard API Client
 * Centralized logic for interacting with the Anomaly Response Agent FastAPI.
 */

// Use localhost consistently to match the browser's address bar and resolve CORS issues on Windows
const API_BASE = "http://localhost:8050/api/v1";

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
  total_llm_cost_usd: number;
}

export interface RewardHistoryEntry {
  incident_id: string;
  reward: number;
  timestamp: string;
  action: string;
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

export async function fetchRewardHistory(): Promise<RewardHistoryEntry[]> {
  const data = await fetchWithLogs(`${API_BASE}/feedback/rewards`, { cache: 'no-store' });
  return data.history || [];
}

export async function fetchDetailedHealth() {
  return fetchWithLogs(`${API_BASE}/health/detailed`, { cache: 'no-store' });
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
