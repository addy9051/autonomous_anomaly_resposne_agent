/**
 * Dashboard API Client
 * Centralized logic for interacting with the Anomaly Response Agent FastAPI.
 */

const API_BASE = "http://127.0.0.1:8000/api/v1";

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

export async function fetchStats() {
  const res = await fetch(`${API_BASE}/status`, { cache: 'no-store' });
  if (!res.ok) throw new Error("Failed to fetch status");
  return res.json();
}

export async function fetchRecentTelemetry(): Promise<TelemetryEvent[]> {
  const res = await fetch(`${API_BASE}/telemetry/recent`, { cache: 'no-store' });
  if (!res.ok) throw new Error("Failed to fetch telemetry");
  const data = await res.json();
  return data.events || [];
}

export async function fetchActiveIncidents(): Promise<IncidentRecord[]> {
  const res = await fetch(`${API_BASE}/incidents/active`, { cache: 'no-store' });
  if (!res.ok) throw new Error("Failed to fetch active incidents");
  const data = await res.json();
  return data.incidents || [];
}

export async function fetchResolvedIncidents(limit = 10): Promise<IncidentRecord[]> {
  const res = await fetch(`${API_BASE}/incidents?limit=${limit}`, { cache: 'no-store' });
  if (!res.ok) throw new Error("Failed to fetch resolved incidents");
  const data = await res.json();
  return data.incidents || [];
}

export async function fetchRewardHistory(): Promise<RewardHistoryEntry[]> {
  const res = await fetch(`${API_BASE}/feedback/rewards`, { cache: 'no-store' });
  if (!res.ok) throw new Error("Failed to fetch rewards");
  const data = await res.json();
  return data.history || [];
}

export async function approveAction(incidentId: string, approved: boolean) {
  const res = await fetch(`${API_BASE}/incidents/${incidentId}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approved }),
  });
  if (!res.ok) throw new Error("Failed to approve action");
  return res.json();
}

export async function triggerDemo() {
  const res = await fetch(`${API_BASE}/events/process`, {
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
  if (!res.ok) throw new Error("Failed to trigger demo");
  return res.json();
}
