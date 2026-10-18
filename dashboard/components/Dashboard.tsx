'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import {
  AgentControls,
  AuditEntry,
  DashboardSession,
  IncidentRecord,
  PerformanceMetrics,
  TelemetryEvent,
  approveAction,
  fetchActiveIncidents,
  fetchAgentControls,
  fetchAuditEntries,
  fetchDetailedHealth,
  fetchPerformanceMetrics,
  fetchRecentTelemetry,
  fetchResolvedIncidents,
  fetchSession,
  saveAgentControls,
  streamIncidentUpdates,
  submitIncidentFeedback,
} from '@/lib/api';

type View = 'command' | 'performance' | 'audit' | 'policies';
type Audience = 'sre' | 'pm';
type Autonomy = 'observe' | 'suggest' | 'approve_to_act' | 'autonomous';
type FeedbackCategory = 'wrong_root_cause' | 'unsafe_fix' | 'too_slow' | 'missed_context' | 'other';
type FeedbackPayload = { verdict: 'helpful' | 'unhelpful'; category?: FeedbackCategory; comment?: string };

const FEEDBACK_CATEGORIES: { value: FeedbackCategory; label: string }[] = [
  { value: 'wrong_root_cause', label: 'Wrong root cause' },
  { value: 'unsafe_fix', label: 'Unsafe fix' },
  { value: 'too_slow', label: 'Too slow' },
  { value: 'missed_context', label: 'Missed context' },
  { value: 'other', label: 'Other' },
];

const AUTONOMY_OPTIONS: { value: Autonomy; label: string; detail: string }[] = [
  { value: 'observe', label: 'Observe', detail: 'Diagnosis only; no action proposal is generated.' },
  { value: 'suggest', label: 'Suggest', detail: 'Recommendations are recorded but never executed.' },
  { value: 'approve_to_act', label: 'Approve to act', detail: 'Every permitted action requires explicit approval.' },
  { value: 'autonomous', label: 'Autonomous', detail: 'Uses existing deterministic policy and allowlists.' },
];

const NAV: { view: View; label: string; icon: IconName }[] = [
  { view: 'command', label: 'Command center', icon: 'grid' },
  { view: 'performance', label: 'Agent performance', icon: 'chart' },
  { view: 'audit', label: 'Audit trail', icon: 'list' },
  { view: 'policies', label: 'Autonomy policy', icon: 'shield' },
];

type IconName = 'grid' | 'chart' | 'list' | 'shield' | 'pulse' | 'arrow' | 'check' | 'close' | 'clock' | 'spark';

function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  const paths: Record<IconName, React.ReactNode> = {
    grid: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
    chart: <><path d="M3 3v18h18" /><path d="m7 14 4-4 4 3 6-7" /></>,
    list: <><path d="M9 6h11M9 12h11M9 18h11" /><path d="M4 6h.01M4 12h.01M4 18h.01" /></>,
    shield: <><path d="M12 22s8-4 8-11V5l-8-3-8 3v6c0 7 8 11 8 11Z" /><path d="m9 12 2 2 4-4" /></>,
    pulse: <><path d="M3 12h4l3-8 4 16 3-8h4" /></>,
    arrow: <><path d="M5 12h14" /><path d="m13 6 6 6-6 6" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    close: <><path d="m18 6-12 12M6 6l12 12" /></>,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    spark: <><path d="m12 3 1.9 5.8L20 11l-6.1 2.2L12 19l-1.9-5.8L4 11l6.1-2.2L12 3Z" /><path d="m19 14 1 3 3 1-3 1-1 3-1-3-3-1 3-1 1-3Z" /></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function serviceName(incident: IncidentRecord): string {
  return incident.anomaly_event?.affected_services?.join(', ') || 'Service not identified';
}

function priority(incident: IncidentRecord): number {
  const severity = incident.anomaly_event?.severity?.toLowerCase();
  const severityRank = severity === 'critical' ? 0 : severity === 'high' ? 1 : severity === 'medium' ? 2 : 3;
  return (incident.status === 'awaiting_approval' || incident.status === 'action_pending' ? -10 : 0) + severityRank;
}

function timeAgo(value?: string): string {
  if (!value) return '—';
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ago`;
}

function percent(value: number | null): string {
  return value === null ? '—' : `${Math.round(value * 100)}%`;
}

function duration(value: number | null): string {
  if (value === null) return '—';
  if (value < 60) return `${Math.round(value)}s`;
  const minutes = value / 60;
  return `${minutes < 10 ? minutes.toFixed(1) : Math.round(minutes)}m`;
}

function pretty(value: string): string {
  return value.replaceAll('_', ' ');
}

export default function Dashboard() {
  const [view, setView] = useState<View>('command');
  const [audience, setAudience] = useState<Audience>('sre');
  const [active, setActive] = useState<IncidentRecord[]>([]);
  const [recent, setRecent] = useState<IncidentRecord[]>([]);
  const [telemetry, setTelemetry] = useState<TelemetryEvent[]>([]);
  const [metrics, setMetrics] = useState<PerformanceMetrics | null>(null);
  const [controls, setControls] = useState<AgentControls>({ paused: false, service_autonomy: {} });
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filter, setFilter] = useState<'all' | 'approval'>('all');
  const [loading, setLoading] = useState(true);
  const [connected, setConnected] = useState(false);
  const [inference, setInference] = useState<'ready' | 'starting' | 'unavailable' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyIncident, setBusyIncident] = useState<string | null>(null);
  const [serviceInput, setServiceInput] = useState('');
  const [autonomyInput, setAutonomyInput] = useState<Autonomy>('approve_to_act');
  const [session, setSession] = useState<DashboardSession | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const canApprove = session?.capabilities.approve ?? false;
  const canControl = session?.capabilities.control ?? false;

  useEffect(() => {
    fetchSession()
      .then((next) => {
        setSession(next);
        if (next.role === 'pm') {
          setAudience('pm');
          setView('performance');
        }
      })
      .catch((err) => setSessionError(messageOf(err)));
  }, []);

  const refreshMetrics = useCallback(async () => {
    try {
      setMetrics(await fetchPerformanceMetrics());
    } catch (err) {
      setError(`Performance data unavailable: ${messageOf(err)}`);
    }
  }, []);

  const refreshAudit = useCallback(async () => {
    try {
      setAudit(await fetchAuditEntries());
    } catch (err) {
      setError(`Audit trail unavailable: ${messageOf(err)}`);
    }
  }, []);

  useEffect(() => {
    let disposed = false;
    void Promise.all([
      fetchActiveIncidents(),
      fetchResolvedIncidents(50),
      fetchRecentTelemetry(),
      fetchPerformanceMetrics(),
      fetchAgentControls(),
      fetchDetailedHealth(),
    ]).then(([activeIncidents, recentIncidents, events, performance, agentControls, health]) => {
      if (disposed) return;
      setActive(activeIncidents);
      setRecent(recentIncidents);
      setTelemetry(events);
      setMetrics(performance);
      setControls(agentControls);
      setInference(health.inference?.chat ?? null);
      if (activeIncidents[0]) setSelectedId(activeIncidents[0].incident_id);
      setConnected(true);
      setError(null);
    }).catch((err: unknown) => {
      if (!disposed) {
        setConnected(false);
        setError(`Could not load control center data: ${messageOf(err)}`);
      }
    }).finally(() => {
      if (!disposed) setLoading(false);
    });
    return () => { disposed = true; };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const receive = (snapshot: { active_incidents: IncidentRecord[]; recent_incidents: IncidentRecord[] }) => {
      setActive(snapshot.active_incidents);
      setRecent(snapshot.recent_incidents);
      setConnected(true);
      setError(null);
      void refreshMetrics();
    };
    const connect = async () => {
      while (!controller.signal.aborted) {
        try {
          await streamIncidentUpdates(controller.signal, receive);
          if (!controller.signal.aborted) throw new Error('Incident stream closed.');
        } catch (err) {
          if (controller.signal.aborted) return;
          setConnected(false);
          setError(`Live updates reconnecting: ${messageOf(err)}`);
          await new Promise<void>((resolve) => window.setTimeout(resolve, 3000));
        }
      }
    };
    void connect();
    return () => controller.abort();
  }, [refreshMetrics]);

  useEffect(() => {
    if (view === 'audit' || view === 'performance') void refreshAudit();
  }, [view, refreshAudit]);

  const queue = useMemo(() => {
    const visible = filter === 'approval'
      ? active.filter((incident) => incident.status === 'awaiting_approval' || incident.status === 'action_pending')
      : active;
    return [...visible].sort((a, b) => priority(a) - priority(b));
  }, [active, filter]);

  const selected = useMemo(
    () => [...active, ...recent].find((incident) => incident.incident_id === selectedId) ?? queue[0] ?? null,
    [active, recent, selectedId, queue],
  );

  const changeAudience = (next: Audience) => {
    setAudience(next);
    setView(next === 'pm' ? 'performance' : 'command');
  };

  const handleApproval = async (incident: IncidentRecord, approved: boolean) => {
    setBusyIncident(incident.incident_id);
    setError(null);
    try {
      await approveAction(incident.incident_id, approved, approved ? 'Approved in control center.' : 'Rejected in control center.');
      setNotice(approved ? 'Action approved. Independent recovery verification is in progress.' : 'Action rejected and incident escalated.');
      await Promise.all([refreshMetrics(), refreshAudit()]);
      const [activeIncidents, recentIncidents] = await Promise.all([fetchActiveIncidents(), fetchResolvedIncidents(50)]);
      setActive(activeIncidents);
      setRecent(recentIncidents);
    } catch (err) {
      setError(`Approval failed: ${messageOf(err)}`);
    } finally {
      setBusyIncident(null);
    }
  };

  const handleFeedback = async (
    incident: IncidentRecord,
    feedback: { verdict: 'helpful' | 'unhelpful'; category?: FeedbackCategory; comment?: string },
  ) => {
    await submitIncidentFeedback(incident.incident_id, feedback);
    const summary = feedback.verdict + (feedback.category ? ` (${feedback.category})` : '');
    const text = feedback.comment ? `${summary}: ${feedback.comment}` : summary;
    const apply = (items: IncidentRecord[]) => items.map((item) => (item.incident_id === incident.incident_id ? { ...item, human_feedback: text } : item));
    setActive(apply);
    setRecent(apply);
    setNotice('Feedback recorded. Thank you.');
    await refreshAudit();
  };

  const updatePause = async () => {
    try {
      const updated = await saveAgentControls({ paused: !controls.paused });
      setControls(updated);
      setNotice(updated.paused ? 'Automatic remediations paused. Investigation and human approvals remain available.' : 'Automatic remediations resumed under existing policy.');
      await refreshAudit();
    } catch (err) {
      setError(`Could not update pause state: ${messageOf(err)}`);
    }
  };

  const updateServiceAutonomy = async (mode: Autonomy | null) => {
    const service = serviceInput.trim();
    if (!service) {
      setError('Enter a service name before saving an autonomy policy.');
      return;
    }
    try {
      const updated = await saveAgentControls({ service, autonomy: mode });
      setControls(updated);
      setNotice(mode ? `${service} autonomy set to ${pretty(mode)}.` : `Custom autonomy for ${service} removed.`);
      setError(null);
      await refreshAudit();
    } catch (err) {
      setError(`Could not save service autonomy: ${messageOf(err)}`);
    }
  };

  const navTo = (next: View) => {
    setView(next);
    if (next === 'performance') setAudience('pm');
    else if (next === 'command') setAudience('sre');
  };

  return (
    <div className="dashboard-shell">
      <aside className="sidebar">
        <Link href="/" className="brand">
          <span className="brand-mark"><Icon name="pulse" size={22} /></span>
          <span><strong>Signal / Response</strong><small>Autonomous operations</small></span>
        </Link>

        <div className="audience-switch" aria-label="Choose workspace">
          <button className={audience === 'sre' ? 'selected' : ''} onClick={() => changeAudience('sre')}>SRE</button>
          <button className={audience === 'pm' ? 'selected' : ''} onClick={() => changeAudience('pm')}>AI Product</button>
        </div>

        <div className="sidebar-label">Workspace</div>
        <nav className="side-nav" aria-label="Main navigation">
          {NAV.map((item) => (
            <button key={item.view} className={`nav-item ${view === item.view ? 'active' : ''}`} onClick={() => navTo(item.view)}>
              <Icon name={item.icon} size={17} />
              <span>{item.label}</span>
              {item.view === 'command' && active.length > 0 && <span className="nav-count">{active.length}</span>}
            </button>
          ))}
          <Link href="/connectors" className="nav-item">
            <Icon name="pulse" size={17} /><span>Connectors</span>
          </Link>
        </nav>

        <div className="sidebar-bottom">
          <div className={`system-card ${connected ? 'online' : 'offline'}`}>
            <span className="live-dot" />
            <div><strong>{connected ? 'Agent connected' : 'Connecting…'}</strong><small>{inference ? `Inference ${inference}` : 'Control plane status'}</small></div>
          </div>
          <span className="sidebar-foot">SRE control plane · v3</span>
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div className="breadcrumb"><span>Workspace</span><span className="crumb-slash">/</span><strong>{NAV.find((item) => item.view === view)?.label}</strong></div>
          <div className="topbar-actions">
            <span className={`connection-label ${connected ? 'connected' : ''}`}><span className="live-dot" />{connected ? 'Live updates' : 'Reconnecting'}</span>
            <span className="connection-label">{session ? `${session.user} · ${session.role === 'sre' ? 'SRE' : 'AI Product'}` : ''}</span>
            <button className={`pause-button ${controls.paused ? 'paused' : ''}`} onClick={updatePause} disabled={!canControl} title={canControl ? undefined : 'Only SREs can pause automation'} aria-pressed={controls.paused}>
              <span>{controls.paused ? '▶' : 'Ⅱ'}</span>{controls.paused ? 'Resume automation' : 'Pause automation'}
            </button>
          </div>
        </header>

        <div className="page-content">
          {sessionError && <div className="notice notice-error" role="alert"><span>Access problem: {sessionError}. Sign in through your organization's SSO and make sure you belong to an SRE or AI Product group.</span></div>}
          {error && <div className="notice notice-error" role="alert"><span>{error}</span><button onClick={() => setError(null)} aria-label="Dismiss error">×</button></div>}
          {notice && <div className="notice notice-success" role="status"><span>{notice}</span><button onClick={() => setNotice(null)} aria-label="Dismiss message">×</button></div>}

          {view === 'command' && (
            <CommandView
              active={active}
              recent={recent}
              telemetry={telemetry}
              metrics={metrics}
              selected={selected}
              queue={queue}
              selectedId={selectedId}
              filter={filter}
              loading={loading}
              busyIncident={busyIncident}
              onSelect={setSelectedId}
              onFilter={setFilter}
              onApproval={handleApproval}
              onFeedback={handleFeedback}
              canApprove={canApprove}
            />
          )}
          {view === 'performance' && <PerformanceView metrics={metrics} incidents={[...active, ...recent]} audit={audit} onOpenAudit={() => setView('audit')} />}
          {view === 'audit' && <AuditView entries={audit} />}
          {view === 'policies' && (
            <PoliciesView
              controls={controls}
              serviceInput={serviceInput}
              autonomyInput={autonomyInput}
              onServiceChange={setServiceInput}
              onAutonomyChange={setAutonomyInput}
              onSave={() => updateServiceAutonomy(autonomyInput)}
              onRemove={() => updateServiceAutonomy(null)}
              onPause={updatePause}
              readOnly={!canControl}
            />
          )}
        </div>
      </main>
    </div>
  );
}

function CommandView({
  active,
  recent,
  telemetry,
  metrics,
  selected,
  queue,
  selectedId,
  filter,
  loading,
  busyIncident,
  onSelect,
  onFilter,
  onApproval,
  onFeedback,
  canApprove,
}: {
  active: IncidentRecord[];
  recent: IncidentRecord[];
  telemetry: TelemetryEvent[];
  metrics: PerformanceMetrics | null;
  selected: IncidentRecord | null;
  queue: IncidentRecord[];
  selectedId: string | null;
  filter: 'all' | 'approval';
  loading: boolean;
  busyIncident: string | null;
  onSelect: (id: string) => void;
  onFilter: (filter: 'all' | 'approval') => void;
  onApproval: (incident: IncidentRecord, approved: boolean) => void;
  onFeedback: (incident: IncidentRecord, feedback: FeedbackPayload) => Promise<void>;
  canApprove: boolean;
}) {
  return (
    <>
      <section className="page-heading">
        <div><span className="eyebrow">SRE workspace</span><h1>Command center</h1><p>Prioritize incidents, inspect evidence, and approve safe action.</p></div>
        <div className="heading-meta"><span className="date-stamp">{new Date().toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' })}</span><span className="stream-chip"><span className="live-dot" />Streaming</span></div>
      </section>

      <section className="summary-grid" aria-label="Reliability summary">
        <SummaryCard label="Active incidents" value={String(active.length)} note={active.length ? `${active.filter((item) => item.status === 'awaiting_approval' || item.status === 'action_pending').length} awaiting your review` : 'No response required'} tone={active.length ? 'amber' : 'green'} icon="pulse" />
        <SummaryCard label="Recovered this week" value={String(metrics?.totals.recovered ?? '—')} note={`${metrics?.totals.auto_resolved ?? 0} autonomously verified`} tone="green" icon="check" />
        <SummaryCard label="Mean time to recover" value={duration(metrics?.timings_seconds.mean_time_to_mitigate ?? null)} note="Incidents in selected 7-day window" tone="blue" icon="clock" />
        <SummaryCard label="Agent spend" value={metrics?.cost.per_incident_usd === null || !metrics ? '—' : `$${metrics.cost.per_incident_usd.toFixed(3)}`} note="Average cost per incident" tone="violet" icon="spark" />
      </section>

      <section className="pulse-panel panel">
        <div className="panel-heading"><div><h2>Service pulse</h2><p>Recent telemetry from connected sources</p></div><span className="muted-label">{telemetry.length} events buffered</span></div>
        {telemetry.length ? <TelemetrySparkline events={telemetry} /> : <div className="empty-chart">Waiting for telemetry from configured connectors.</div>}
      </section>

      <div className="response-layout">
        <section className="incident-panel panel">
          <div className="panel-heading">
            <div><h2>Incident queue <span className="heading-count">{queue.length}</span></h2><p>Sorted by approval need and severity</p></div>
            <div className="filter-switch" aria-label="Filter incidents">
              <button className={filter === 'all' ? 'selected' : ''} onClick={() => onFilter('all')}>All active</button>
              <button className={filter === 'approval' ? 'selected' : ''} onClick={() => onFilter('approval')}>Needs approval</button>
            </div>
          </div>
          {loading ? <div className="panel-placeholder">Loading incident queue…</div> : queue.length === 0 ? (
            <div className="empty-queue"><span className="empty-check"><Icon name="check" size={22} /></span><strong>{filter === 'approval' ? 'No approvals waiting' : 'You’re all caught up'}</strong><span>{filter === 'approval' ? 'New proposed actions will appear here.' : 'The agent has no active incidents to investigate.'}</span></div>
          ) : (
            <div className="incident-table-wrap">
              <table className="incident-table">
                <thead><tr><th>Incident</th><th>Service</th><th>Agent state</th><th>Age</th><th /></tr></thead>
                <tbody>
                  {queue.map((incident) => (
                    <tr key={incident.incident_id} className={incident.incident_id === selectedId ? 'selected-row' : ''} onClick={() => onSelect(incident.incident_id)}>
                      <td><div className="incident-name"><span className={`severity-dot severity-${incident.anomaly_event?.severity?.toLowerCase() ?? 'unknown'}`} /><span><strong>{pretty(incident.anomaly_event?.anomaly_type ?? 'incident').replace(/^./, (char) => char.toUpperCase())}</strong><small>#{incident.incident_id.slice(0, 8)}</small></span></div></td>
                      <td className="service-cell">{serviceName(incident)}</td>
                      <td><StatusBadge status={incident.status} /></td>
                      <td className="age-cell">{timeAgo(incident.created_at)}</td>
                      <td><Icon name="arrow" size={15} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <IncidentDetail incident={selected} busy={busyIncident === selected?.incident_id} onApproval={onApproval} onFeedback={onFeedback} canApprove={canApprove} />
      </div>

      <section className="recent-strip">
        <span>Recently updated</span>
        {recent.filter((item) => !active.some((live) => live.incident_id === item.incident_id)).slice(0, 4).map((incident) => (
          <button key={incident.incident_id} onClick={() => onSelect(incident.incident_id)}>
            <span className={`severity-dot severity-${incident.anomaly_event?.severity?.toLowerCase() ?? 'unknown'}`} />
            <strong>#{incident.incident_id.slice(0, 8)}</strong><span>{pretty(incident.status)}</span><small>{timeAgo(incident.updated_at ?? incident.created_at)}</small>
          </button>
        ))}
      </section>
    </>
  );
}

function SummaryCard({ label, value, note, tone, icon }: { label: string; value: string; note: string; tone: string; icon: IconName }) {
  return <article className="summary-card panel"><span className={`summary-icon ${tone}`}><Icon name={icon} size={18} /></span><span className="summary-label">{label}</span><strong className="summary-value">{value}</strong><span className="summary-note">{note}</span></article>;
}

function TelemetrySparkline({ events }: { events: TelemetryEvent[] }) {
  const points = events.slice(-32).map((event, index) => {
    const metric = Number(event.payload?.p99_latency_ms ?? event.payload?.latency_ms ?? event.payload?.error_rate ?? 0);
    return { x: events.length <= 1 ? 0 : (index / (Math.min(events.length, 32) - 1)) * 100, value: Number.isFinite(metric) ? metric : 0 };
  });
  const max = Math.max(...points.map((point) => point.value), 1);
  const polyline = points.map((point) => `${point.x},${40 - (point.value / max) * 34}`).join(' ');
  return <div className="sparkline-wrap"><svg viewBox="0 0 100 44" preserveAspectRatio="none" role="img" aria-label="Recent telemetry trend"><polyline points={polyline} /></svg><div className="sparkline-caption"><span>OLDER EVENTS</span><span>NOW</span></div></div>;
}

function FeedbackControl({ incident, onFeedback }: { incident: IncidentRecord; onFeedback: (incident: IncidentRecord, feedback: FeedbackPayload) => Promise<void> }) {
  const [verdict, setVerdict] = useState<'helpful' | 'unhelpful' | null>(null);
  const [category, setCategory] = useState<FeedbackCategory | ''>('');
  const [comment, setComment] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  // A different incident must start with a clean form.
  useEffect(() => {
    setVerdict(null);
    setCategory('');
    setComment('');
    setFailure(null);
  }, [incident.incident_id]);

  const needsCategory = verdict === 'unhelpful';
  const canSubmit = verdict !== null && (!needsCategory || category !== '') && !submitting;

  const submit = async () => {
    if (!verdict) return;
    setSubmitting(true);
    setFailure(null);
    try {
      await onFeedback(incident, {
        verdict,
        category: needsCategory && category ? category : undefined,
        comment: comment.trim() || undefined,
      });
      setVerdict(null);
      setCategory('');
      setComment('');
    } catch (err) {
      setFailure(messageOf(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="detail-section feedback-control">
      <div className="section-heading"><h3>Review the agent's handling</h3><span className="muted-label">Improves quality tracking</span></div>
      {incident.human_feedback && <p className="feedback-existing"><strong>Latest review</strong>{incident.human_feedback}</p>}
      <div className="feedback-verdict" role="group" aria-label="Was the agent's handling helpful?">
        <button type="button" className={verdict === 'helpful' ? 'selected' : ''} aria-pressed={verdict === 'helpful'} onClick={() => setVerdict('helpful')}>Helpful</button>
        <button type="button" className={verdict === 'unhelpful' ? 'selected negative' : ''} aria-pressed={verdict === 'unhelpful'} onClick={() => setVerdict('unhelpful')}>Not helpful</button>
      </div>
      {verdict && (
        <>
          {needsCategory && (
            <>
              <label className="field-label" htmlFor={`feedback-category-${incident.incident_id}`}>What went wrong?</label>
              <select id={`feedback-category-${incident.incident_id}`} className="text-input" value={category} onChange={(event) => setCategory(event.target.value as FeedbackCategory | '')}>
                <option value="">Select a reason</option>
                {FEEDBACK_CATEGORIES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
            </>
          )}
          <label className="field-label" htmlFor={`feedback-comment-${incident.incident_id}`}>Comment <span className="optional">(optional)</span></label>
          <textarea id={`feedback-comment-${incident.incident_id}`} className="text-input feedback-comment" rows={3} maxLength={2000} value={comment} onChange={(event) => setComment(event.target.value)} placeholder="What should the agent have done differently?" />
          {failure && <p className="feedback-error" role="alert">{failure}</p>}
          <button type="button" className="button-primary feedback-submit" disabled={!canSubmit} onClick={submit}>{submitting ? 'Submitting…' : 'Submit review'}</button>
        </>
      )}
    </div>
  );
}

function IncidentDetail({ incident, busy, onApproval, onFeedback, canApprove }: { incident: IncidentRecord | null; busy: boolean; onApproval: (incident: IncidentRecord, approved: boolean) => void; onFeedback: (incident: IncidentRecord, feedback: FeedbackPayload) => Promise<void>; canApprove: boolean }) {
  if (!incident) return <aside className="detail-panel panel detail-empty"><div className="detail-empty-mark"><Icon name="spark" size={22} /></div><h2>Incident detail</h2><p>Select an incident from the queue to review its diagnosis and evidence.</p></aside>;

  const diagnosis = incident.diagnosis_result;
  const pendingAction = incident.action_results.find((action) => action.execution_status === 'pending_approval');
  const confidence = diagnosis?.confidence;
  const timeline = [
    { label: 'Detected', value: incident.created_at, done: true },
    { label: 'Diagnosis', value: diagnosis?.root_cause_category ? pretty(diagnosis.root_cause_category) : 'In progress', done: Boolean(diagnosis) },
    { label: 'Action', value: incident.action_results[0]?.execution_status ? pretty(incident.action_results[0].execution_status) : 'No action recorded', done: incident.action_results.length > 0 },
    { label: 'Verification', value: incident.verification_result?.outcome ? pretty(incident.verification_result.outcome) : 'Pending', done: Boolean(incident.verification_result) },
  ];

  return (
    <aside className="detail-panel panel">
      <div className="detail-title-row"><div><span className={`severity-pill severity-${incident.anomaly_event?.severity?.toLowerCase() ?? 'unknown'}`}>{incident.anomaly_event?.severity ?? 'Unknown severity'}</span><span className="incident-id">#{incident.incident_id.slice(0, 8)}</span></div><StatusBadge status={incident.status} /></div>
      <h2 className="detail-title">{pretty(incident.anomaly_event?.anomaly_type ?? 'Incident').replace(/^./, (char) => char.toUpperCase())}</h2>
      <p className="detail-service">{serviceName(incident)} · {timeAgo(incident.created_at)}</p>

      <div className="detail-section">
        <div className="section-heading"><h3>Agent assessment</h3>{confidence !== undefined && <span className="confidence-tag">{Math.round(confidence * 100)}% confidence</span>}</div>
        {diagnosis ? (
          <>
            <strong className="root-cause">{diagnosis.root_cause ?? 'Root cause not available'}</strong>
            <p className="decision-summary">{diagnosis.decision_summary || 'No responder-facing decision summary was recorded.'}</p>
          </>
        ) : <p className="detail-muted">Diagnosis is still running. Evidence will appear here as it becomes available.</p>}
      </div>

      {diagnosis?.hypotheses && diagnosis.hypotheses.length > 0 && (
        <div className="detail-section">
          <div className="section-heading"><h3>Root-cause hypotheses</h3><span className="muted-label">{diagnosis.hypotheses.length} candidates</span></div>
          <div className="hypothesis-list">{diagnosis.hypotheses.map((item, index) => (
            <article className="hypothesis-card" key={`${item.hypothesis}-${index}`}><div><strong>{item.hypothesis}</strong><span className={`confidence-band band-${item.confidence_band}`}>{item.confidence_band} confidence</span></div>{item.affected_scope?.length ? <small>Scope: {item.affected_scope.join(', ')}</small> : null}{item.missing_evidence?.length ? <small>Missing: {item.missing_evidence.join('; ')}</small> : null}</article>
          ))}</div>
        </div>
      )}

      {diagnosis?.evidence_timeline && diagnosis.evidence_timeline.length > 0 && (
        <div className="detail-section">
          <div className="section-heading"><h3>Evidence</h3><span className="muted-label">{diagnosis.evidence_timeline.length} observations</span></div>
          <ul className="evidence-list">{diagnosis.evidence_timeline.slice(0, 5).map((item, index) => <li key={`${item.source}-${index}`}><span className="evidence-source">{item.source}</span><span>{item.description || 'Observation recorded'}</span>{item.value !== undefined && <strong>{String(item.value)}{item.unit ? ` ${item.unit}` : ''}</strong>}</li>)}</ul>
        </div>
      )}

      <div className="detail-section">
        <div className="section-heading"><h3>Response timeline</h3><span className="muted-label">Incident lifecycle</span></div>
        <ol className="timeline-list">{timeline.map((step) => <li key={step.label} className={step.done ? 'done' : ''}><span className="timeline-marker" /><div><strong>{step.label}</strong><small>{step.value && (step.label === 'Detected' ? timeAgo(step.value) : step.value)}</small></div></li>)}</ol>
      </div>

      {incident.verification_result && <div className="verification-box"><Icon name="check" size={17} /><span><strong>{pretty(incident.verification_result.outcome)}</strong>{incident.verification_result.details ? ` · ${incident.verification_result.details}` : ''}</span></div>}

      {pendingAction && <div className="approval-card"><div><strong>Action approval required</strong><p>{pretty(pendingAction.action_taken)} is waiting for an SRE decision.</p></div>{canApprove ? <div className="approval-buttons"><button className="button-secondary" disabled={busy} onClick={() => onApproval(incident, false)}><Icon name="close" size={15} />Reject</button><button className="button-primary" disabled={busy} onClick={() => onApproval(incident, true)}><Icon name="check" size={15} />{busy ? 'Submitting…' : 'Approve action'}</button></div> : <small>Only SREs can approve actions.</small>}</div>}

            <FeedbackControl incident={incident} onFeedback={onFeedback} />
          </aside>
  );
}

function StatusBadge({ status }: { status: string }) {
  const tone = status.includes('approval') || status.includes('pending') ? 'warning' : status.includes('recover') || status === 'resolved' ? 'success' : status === 'escalated' || status === 'failed' ? 'danger' : 'neutral';
  return <span className={`status-badge ${tone}`}><span />{pretty(status)}</span>;
}

function PerformanceView({ metrics, incidents, audit, onOpenAudit }: { metrics: PerformanceMetrics | null; incidents: IncidentRecord[]; audit: AuditEntry[]; onOpenAudit: () => void }) {
  const values = metrics;
  const diagnosedCount = values?.funnel.diagnosed ?? 0;
  const funnel = values ? [
    { label: 'Detected', value: values.funnel.detected },
    { label: 'Diagnosed', value: values.funnel.diagnosed },
    { label: 'Recommendation', value: values.funnel.with_recommendations },
    { label: 'Action recorded', value: values.funnel.with_actions_recorded },
    { label: 'Verified recovered', value: values.funnel.verified_recovered },
  ] : [];
  const latest = [...incidents].sort((a, b) => (b.created_at || '').localeCompare(a.created_at || '')).slice(0, 6);
  return (
    <>
      <section className="page-heading"><div><span className="eyebrow">AI product workspace</span><h1>Agent performance</h1><p>Operational outcomes, intervention rates, and cost from recorded incidents.</p></div><span className="window-chip">{values?.window ?? '7d'} window</span></section>
      <section className="metric-grid">
        <MetricCard label="Verified recovery rate" value={percent(values?.rates.resolution ?? null)} detail={`${values?.totals.recovered ?? 0} of ${values?.totals.incidents ?? 0} incidents verified recovered`} />
        <MetricCard label="Autonomous resolution" value={percent(values?.rates.autonomous_resolution ?? null)} detail={`${values?.totals.auto_resolved ?? 0} auto-resolved incidents`} />
        <MetricCard label="Human override rate" value={percent(values?.rates.human_override ?? null)} detail={`${values?.totals.human_overrides ?? 0} incidents marked overridden`} />
        <MetricCard label="False-positive rate" value={percent(values?.rates.false_positive ?? null)} detail={`${values?.totals.false_positives ?? 0} incidents labelled false positive`} />
        <MetricCard label="Mean time to detect" value={duration(values?.timings_seconds.mean_time_to_detect ?? null)} detail="Average, where timing was recorded" />
        <MetricCard label="Mean time to mitigate" value={duration(values?.timings_seconds.mean_time_to_mitigate ?? null)} detail="Average, where timing was recorded" />
        <MetricCard label="Cost per incident" value={values?.cost.per_incident_usd === null || !values ? '—' : `$${values.cost.per_incident_usd.toFixed(3)}`} detail={`${values?.cost.total_tokens.toLocaleString() ?? '—'} tokens in this window`} />
        <MetricCard label="Tokens per incident" value={values?.cost.tokens_per_incident?.toLocaleString() ?? '—'} detail={`$${(values?.cost.total_usd ?? 0).toFixed(3)} total model cost`} />
      </section>

      <div className="performance-columns">
        <section className="panel performance-panel">
          <div className="panel-heading"><div><h2>Incident trust funnel</h2><p>Counts from persisted incident lifecycle records</p></div><span className="muted-label">{values?.totals.incidents ?? 0} detected</span></div>
          {funnel.map((stage) => <div className="funnel-row" key={stage.label}><div><span>{stage.label}</span><strong>{stage.value}</strong></div><div className="funnel-track"><span style={{ width: `${values?.funnel.detected ? Math.max(2, (stage.value / values.funnel.detected) * 100) : 0}%` }} /></div></div>)}
        </section>
        <section className="panel performance-panel">
          <div className="panel-heading"><div><h2>Signals to strengthen</h2><p>Data-quality notes for interpreting this view</p></div><Icon name="spark" /></div>
          <ul className="quality-notes">
            <li><strong>Root-cause correctness</strong><span>No reviewer outcome labels are currently stored; diagnosis confidence is not an accuracy score.</span></li>
            <li><strong>Regression tracking</strong><span>{incidents.filter((item) => item.regressed).length} regressions are present in the latest loaded incident sample.</span></li>
            <li><strong>Scope</strong><span>Rates use incidents created inside the selected window. Missing timings are excluded from timing averages.</span></li>
          </ul>
          <div className="funnel-footnote">Mean confidence among diagnosed: {diagnosedCount ? `${Math.round((incidents.reduce((sum, item) => sum + (item.diagnosis_result?.confidence ?? 0), 0) / Math.max(1, incidents.filter((item) => item.diagnosis_result?.confidence !== undefined).length)) * 100)}% in the latest loaded records` : 'not available'}.</div>
        </section>
      </div>

      <section className="panel performance-panel recent-performance">
        <div className="panel-heading"><div><h2>Recent incident outcomes</h2><p>Drill into the records behind the aggregate metrics.</p></div></div>
        {latest.length ? <div className="outcome-list">{latest.map((item) => <div key={item.incident_id}><span className={`severity-dot severity-${item.anomaly_event?.severity?.toLowerCase() ?? 'unknown'}`} /><strong>#{item.incident_id.slice(0, 8)}</strong><span>{serviceName(item)}</span><StatusBadge status={item.status} /><small>{timeAgo(item.created_at)}</small></div>)}</div> : <div className="panel-placeholder">No incidents recorded in this view.</div>}
      </section>

              <section className="panel performance-panel governance-log">
                <div className="panel-heading"><div><h2>Governance audit log</h2><p>Latest agent actions, approvals, control changes and reviews.</p></div><button type="button" className="button-text" onClick={onOpenAudit}>View full trail</button></div>
                {audit.length ? <div className="governance-list">{audit.slice(0, 8).map((entry) => (
                  <details key={entry.id}>
                    <summary><time>{new Date(entry.timestamp).toLocaleString()}</time><strong>{pretty(entry.action)}</strong><span className="audit-actor">{entry.actor}</span><code>{entry.incident_id.slice(0, 8)}</code></summary>
                    <pre>{JSON.stringify(entry.details, null, 2)}</pre>
                  </details>
                ))}</div> : <div className="panel-placeholder">No audit entries recorded yet.</div>}
              </section>
            </>
  );
}

function MetricCard({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <article className="metric-card panel"><span>{label}</span><strong>{value}</strong><small>{detail}</small></article>;
}

function AuditView({ entries }: { entries: AuditEntry[] }) {
  return (
    <>
      <section className="page-heading"><div><span className="eyebrow">Governance</span><h1>Audit trail</h1><p>Chronological record of incident handling and control changes.</p></div><span className="window-chip">{entries.length} recent records</span></section>
      <section className="panel audit-panel">
        {!entries.length ? <div className="empty-queue"><span className="empty-check"><Icon name="list" /></span><strong>No audit entries yet</strong><span>Agent and human actions will be listed here.</span></div> : entries.map((entry) => (
          <article className="audit-row" key={entry.id}><div className="audit-time">{new Date(entry.timestamp).toLocaleString()}</div><div className="audit-marker"><span /></div><div className="audit-content"><div><strong>{pretty(entry.action)}</strong><span className="audit-actor">{entry.actor}</span></div><p>Incident <code>{entry.incident_id}</code></p>{Object.keys(entry.details).length > 0 && <details><summary>View event details</summary><pre>{JSON.stringify(entry.details, null, 2)}</pre></details>}</div></article>
        ))}
      </section>
    </>
  );
}

function PoliciesView({
  controls,
  serviceInput,
  autonomyInput,
  onServiceChange,
  onAutonomyChange,
  onSave,
  onRemove,
  onPause,
  readOnly,
}: {
  controls: AgentControls;
  serviceInput: string;
  autonomyInput: Autonomy;
  onServiceChange: (value: string) => void;
  onAutonomyChange: (value: Autonomy) => void;
  onSave: () => void;
  onRemove: () => void;
  onPause: () => void;
  readOnly: boolean;
}) {
  return (
    <>
      <section className="page-heading"><div><span className="eyebrow">Safety &amp; governance</span><h1>Autonomy policy</h1><p>Set operational guardrails. Service controls never override deterministic safety checks.</p></div></section>
      <section className="panel global-control-card">
        <div className="global-control-copy"><span className="control-icon"><Icon name="shield" size={20} /></span><div><h2>Global automation pause</h2><p>{controls.paused ? 'Automatic remediations are paused. Investigation and human-approved actions remain available.' : 'Automatic actions are governed by each service policy and the existing action allowlist.'}</p></div></div>
        <button className={controls.paused ? 'button-primary' : 'button-secondary'} onClick={onPause} disabled={readOnly}>{controls.paused ? 'Resume automation' : 'Pause automation'}</button>
      </section>

      <section className="policy-layout">
        <div className="panel policy-editor">
          <div className="panel-heading"><div><h2>Service autonomy</h2><p>Set a control for a named service.</p></div></div>
          <label className="field-label" htmlFor="service-name">Service name</label>
          <input id="service-name" className="text-input" disabled={readOnly} value={serviceInput} onChange={(event) => onServiceChange(event.target.value)} placeholder="e.g. checkout-api" />
          <label className="field-label" htmlFor="autonomy-level">Autonomy level</label>
          <select id="autonomy-level" className="text-input" disabled={readOnly} value={autonomyInput} onChange={(event) => onAutonomyChange(event.target.value as Autonomy)}>
            {AUTONOMY_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
          <p className="policy-description">{AUTONOMY_OPTIONS.find((option) => option.value === autonomyInput)?.detail}</p>
          <div className="policy-actions"><button className="button-primary" disabled={readOnly} onClick={onSave}>Save service policy</button><button className="button-text" disabled={readOnly} onClick={onRemove}>Remove override</button></div>
        </div>
        <div className="panel policy-current">
          <div className="panel-heading"><div><h2>Configured services</h2><p>Overrides stored by the backend</p></div></div>
          {Object.entries(controls.service_autonomy).length === 0 ? <div className="panel-placeholder">No service overrides. The existing deterministic policy applies.</div> : Object.entries(controls.service_autonomy).map(([service, mode]) => <div className="service-policy-row" key={service}><div><strong>{service}</strong><small>{AUTONOMY_OPTIONS.find((option) => option.value === mode)?.detail}</small></div><span className="status-badge neutral">{pretty(mode)}</span></div>)}
        </div>
      </section>
      <section className="policy-footnote"><strong>Safety guarantee</strong><span>“Autonomous” does not enable new actions. Registered workflows, parameter validation, environment controls, severity restrictions, and the configured auto-remediation allowlist remain authoritative.</span></section>
    </>
  );
}
