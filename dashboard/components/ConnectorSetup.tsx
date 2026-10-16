'use client';

import React, { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import {
  ConnectorField,
  ConnectorsView,
  activateIngestion,
  deactivateIngestion,
  fetchConnectors,
  saveConnectorCredentials,
  saveConnectorSelection,
  testConnector,
} from '@/lib/api';
import { ConnectorIcon } from './ConnectorIcons';

type FormValues = Record<string, Record<string, string | boolean>>;
type TestResult = { ok: boolean; detail: string } | 'testing';

const STEPS = ['Select connectors', 'Credentials', 'Activate'];

function errorText(err: unknown): string {
  const msg = err instanceof Error ? err.message : String(err);
  const json = msg.slice(msg.indexOf('{'));
  try {
    const detail = JSON.parse(json).detail;
    return typeof detail === 'string' ? detail : JSON.stringify(detail);
  } catch {
    return msg;
  }
}

function initialForms(view: ConnectorsView): FormValues {
  const forms: FormValues = {};
  for (const c of view.connectors) {
    forms[c.id] = {};
    for (const f of c.fields) {
      if (f.secret) continue;
      forms[c.id][f.key] = f.kind === 'boolean' ? Boolean(f.value) : String(f.value ?? '');
    }
  }
  return forms;
}

// Secrets left blank are omitted so the stored value is kept.
function payloadFor(form: Record<string, string | boolean>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(form)) {
    if (v !== '') out[k] = v;
  }
  return out;
}

export default function ConnectorSetup() {
  const [view, setView] = useState<ConnectorsView | null>(null);
  const [step, setStep] = useState(0);
  const [selected, setSelected] = useState<string[]>([]);
  const [forms, setForms] = useState<FormValues>({});
  const [tests, setTests] = useState<Record<string, TestResult>>({});
  const [services, setServices] = useState('');
  const [dryRun, setDryRun] = useState(true);
  const [pollSeconds, setPollSeconds] = useState(15);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);

  const load = useCallback(async (initial: boolean) => {
    try {
      const v = await fetchConnectors();
      setView(v);
      if (initial) {
        setSelected(v.selected);
        setForms(initialForms(v));
        setServices(v.ingestion.services);
        setDryRun(v.ingestion.dry_run);
        setPollSeconds(v.ingestion.poll_interval_seconds);
        if (v.active) setStep(2);
      }
    } catch {
      setLoadFailed(true);
    }
  }, []);

  useEffect(() => {
    load(true);
  }, [load]);

  // Keep vendor health fresh while ingestion is running.
  useEffect(() => {
    if (!view?.active) return;
    const t = setInterval(() => load(false), 5000);
    return () => clearInterval(t);
  }, [view?.active, load]);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const toggle = (id: string) =>
    setSelected((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));

  const setField = (vendor: string, key: string, value: string | boolean) =>
    setForms((f) => ({ ...f, [vendor]: { ...f[vendor], [key]: value } }));

  const goCredentials = () =>
    run(async () => {
      setView(await saveConnectorSelection(selected));
      setStep(1);
    });

  const goActivate = () =>
    run(async () => {
      let latest: ConnectorsView | null = null;
      for (const id of selected) {
        latest = await saveConnectorCredentials(id, payloadFor(forms[id] ?? {}));
      }
      if (latest) setView(latest);
      const unfinished = (latest ?? view)?.connectors.filter((c) => selected.includes(c.id) && !c.configured) ?? [];
      if (unfinished.length) {
        throw new Error(unfinished.map((c) => `${c.label}: missing ${c.missing.join(', ')}`).join('; '));
      }
      setStep(2);
    });

  const runTest = async (id: string) => {
    setTests((t) => ({ ...t, [id]: 'testing' }));
    try {
      const res = await testConnector(id, payloadFor(forms[id] ?? {}));
      setTests((t) => ({ ...t, [id]: res }));
    } catch (err) {
      setTests((t) => ({ ...t, [id]: { ok: false, detail: errorText(err) } }));
    }
  };

  const activate = () =>
    run(async () => {
      setView(await activateIngestion({ services, dry_run: dryRun, poll_interval_seconds: pollSeconds }));
    });

  const stop = () => run(async () => setView(await deactivateIngestion()));

  if (loadFailed) {
    return (
      <Shell>
        <p className="text-rose-400 text-sm font-bold">
          Cannot reach the orchestrator API. Check that the backend is running and that NEXT_PUBLIC_API_BASE
          {' '}(and NEXT_PUBLIC_API_KEY if API_AUTH_TOKEN is set) are correct.
        </p>
      </Shell>
    );
  }
  if (!view) {
    return (
      <Shell>
        <p className="text-white/40 text-sm">Loading…</p>
      </Shell>
    );
  }

  const selectedInfo = view.connectors.filter((c) => selected.includes(c.id));
  const modes = selected.join(',');

  return (
    <Shell>
      <ol className="flex items-center gap-3 mb-8 text-xs font-bold uppercase tracking-widest">
        {STEPS.map((label, i) => (
          <li key={label} className={`flex items-center gap-2 ${i === step ? 'text-primary' : i < step ? 'text-white/70' : 'text-white/30'}`}>
            <span className={`w-6 h-6 rounded-full flex items-center justify-center border ${i <= step ? 'border-primary' : 'border-white/20'}`}>
              {i + 1}
            </span>
            {label}
            {i < STEPS.length - 1 && <span className="w-8 h-px bg-white/10" />}
          </li>
        ))}
      </ol>

      {error && (
        <div role="alert" className="mb-6 rounded-2xl border border-rose-500/30 bg-rose-500/10 p-4 text-xs font-bold text-rose-400">
          {error}
        </div>
      )}

      {step === 0 && (
        <section>
          <h2 className="text-xl font-black mb-1">Choose your telemetry sources</h2>
          <p className="text-sm text-white/50 mb-6">Select one or more. Signals from all selected sources are combined into a single stream.</p>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {view.connectors.map((c) => {
              const on = selected.includes(c.id);
              return (
                <button
                  key={c.id}
                  type="button"
                  role="checkbox"
                  aria-checked={on}
                  onClick={() => toggle(c.id)}
                  className={`glass text-left rounded-3xl p-6 transition-all border-2 ${on ? 'border-primary shadow-neon' : 'border-transparent hover:border-white/20'}`}
                >
                  <div className="flex items-start justify-between mb-4">
                    <ConnectorIcon id={c.id} size={48} />
                    <span className={`w-6 h-6 rounded-md border flex items-center justify-center text-xs font-black ${on ? 'bg-primary border-primary text-black' : 'border-white/30'}`}>
                      {on ? '✓' : ''}
                    </span>
                  </div>
                  <div className="font-black text-lg">{c.label}</div>
                  <p className="text-xs text-white/50 mt-1">{c.description}</p>
                  {c.configured && <span className="inline-block mt-3 text-[10px] font-bold uppercase text-emerald-400">Credentials saved</span>}
                </button>
              );
            })}
          </div>
          <Footer>
            <span className="text-xs text-white/40">{selected.length} selected</span>
            <Primary disabled={busy || selected.length === 0} onClick={goCredentials}>Continue</Primary>
          </Footer>
        </section>
      )}

      {step === 1 && (
        <section>
          <h2 className="text-xl font-black mb-1">Connect your accounts</h2>
          <p className="text-sm text-white/50 mb-6">Secrets are sent to the server over your session and are never shown again. Leave a saved secret blank to keep it.</p>
          <div className="space-y-6">
            {selectedInfo.map((c) => {
              const t = tests[c.id];
              return (
                <div key={c.id} className="glass rounded-3xl p-6">
                  <div className="flex items-center gap-3 mb-5">
                    <ConnectorIcon id={c.id} size={36} />
                    <h3 className="font-black text-lg">{c.label}</h3>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {c.fields.map((f) => (
                      <FieldInput
                        key={f.key}
                        field={f}
                        id={`${c.id}-${f.key}`}
                        value={forms[c.id]?.[f.key] ?? (f.kind === 'boolean' ? false : '')}
                        onChange={(v) => setField(c.id, f.key, v)}
                      />
                    ))}
                  </div>
                  <div className="flex items-center gap-4 mt-5">
                    <button
                      type="button"
                      onClick={() => runTest(c.id)}
                      disabled={t === 'testing'}
                      className="px-4 py-2 rounded-xl bg-white/10 hover:bg-white/20 text-xs font-bold uppercase tracking-widest disabled:opacity-50"
                    >
                      {t === 'testing' ? 'Testing…' : 'Test connection'}
                    </button>
                    {t && t !== 'testing' && (
                      <span className={`text-xs font-bold ${t.ok ? 'text-emerald-400' : 'text-rose-400'}`}>{t.detail}</span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
          <Footer>
            <button type="button" onClick={() => setStep(0)} className="text-xs font-bold uppercase text-white/50 hover:text-white">Back</button>
            <Primary disabled={busy} onClick={goActivate}>Save &amp; continue</Primary>
          </Footer>
        </section>
      )}

      {step === 2 && (
        <section>
          <h2 className="text-xl font-black mb-1">Activate combined ingestion</h2>
          <p className="text-sm text-white/50 mb-6">Tell the agent which services to watch. It polls every selected source and escalates only anomalous services.</p>

          <div className="glass rounded-3xl p-6 space-y-5">
            <div className="text-xs font-mono text-white/60">
              CONNECTOR_MODES=<span className="text-primary">{modes || '(none)'}</span>
            </div>
            <label className="block">
              <span className="text-xs font-bold uppercase text-white/50">Services to watch (comma-separated)</span>
              <input
                value={services}
                onChange={(e) => setServices(e.target.value)}
                placeholder="checkout,payments"
                className="mt-1 w-full rounded-xl bg-black/40 border border-white/10 px-4 py-3 text-sm"
              />
            </label>
            <div className="flex flex-wrap gap-6 items-center">
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
                Dry-run (no real remediation actions)
              </label>
              <label className="flex items-center gap-2 text-sm">
                Poll every
                <input
                  type="number"
                  min={1}
                  value={pollSeconds}
                  onChange={(e) => setPollSeconds(Number(e.target.value))}
                  className="w-20 rounded-lg bg-black/40 border border-white/10 px-2 py-1"
                />
                seconds
              </label>
            </div>
          </div>

          {view.active && view.status && (
            <div className="glass rounded-3xl p-6 mt-6">
              <div className="flex justify-between mb-4">
                <h3 className="font-black text-sm uppercase tracking-widest text-primary">Live — watching {view.status.services.join(', ')}</h3>
                <span className="text-xs text-white/50">{view.status.escalations} escalations</span>
              </div>
              <ul className="space-y-3">
                {Object.entries(view.status.vendors).map(([name, s]) => (
                  <li key={name} className="flex items-center gap-3 text-sm">
                    <ConnectorIcon id={name} size={24} />
                    <span className="font-bold w-24 capitalize">{name}</span>
                    <span className={s.consecutive_failures ? 'text-rose-400' : 'text-emerald-400'}>
                      {s.consecutive_failures
                        ? `failing (${s.consecutive_failures}): ${s.last_error ?? ''}`
                        : s.last_success_age_seconds === null
                          ? 'waiting for first poll'
                          : `ok · ${s.last_success_age_seconds}s ago`}
                    </span>
                    <span className="ml-auto text-xs text-white/40">{s.records_ingested} records</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {view.ingestion.enabled && !view.active && (
            <p className="mt-6 text-xs font-bold text-amber-400">Ingestion is enabled but not running: check the connector credentials and the server log.</p>
          )}

          <Footer>
            <button type="button" onClick={() => setStep(1)} className="text-xs font-bold uppercase text-white/50 hover:text-white">Back</button>
            <div className="flex gap-3">
              {view.active && (
                <button type="button" onClick={stop} disabled={busy} className="px-5 py-3 rounded-2xl bg-rose-500/20 text-rose-300 text-xs font-bold uppercase tracking-widest disabled:opacity-50">
                  Stop
                </button>
              )}
              <Primary disabled={busy || !services.trim()} onClick={activate}>{view.active ? 'Apply changes' : 'Activate'}</Primary>
            </div>
          </Footer>
        </section>
      )}
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen p-8 max-w-5xl mx-auto">
      <Link href="/" className="text-xs font-bold uppercase tracking-widest text-white/40 hover:text-primary">← Mission control</Link>
      <h1 className="text-2xl font-black uppercase tracking-tight mt-4 mb-8">Data connectors</h1>
      {children}
    </div>
  );
}

function Footer({ children }: { children: React.ReactNode }) {
  return <div className="flex items-center justify-between mt-8">{children}</div>;
}

function Primary({ children, onClick, disabled }: { children: React.ReactNode; onClick: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="px-6 py-3 rounded-2xl bg-primary text-black text-xs font-black uppercase tracking-widest disabled:opacity-40"
    >
      {children}
    </button>
  );
}

function FieldInput({
  field,
  id,
  value,
  onChange,
}: {
  field: ConnectorField;
  id: string;
  value: string | boolean;
  onChange: (v: string | boolean) => void;
}) {
  if (field.kind === 'boolean') {
    return (
      <label htmlFor={id} className="flex items-center gap-2 text-sm md:col-span-2">
        <input id={id} type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />
        {field.label}
      </label>
    );
  }
  return (
    <label htmlFor={id} className="block">
      <span className="text-xs font-bold uppercase text-white/50">
        {field.label}
        {field.required && <span className="text-rose-400"> *</span>}
      </span>
      <input
        id={id}
        type={field.secret ? 'password' : field.kind === 'number' ? 'number' : 'text'}
        autoComplete="off"
        value={String(value)}
        onChange={(e) => onChange(e.target.value)}
        placeholder={field.secret && field.is_set ? '•••••••• (saved)' : field.placeholder}
        className="mt-1 w-full rounded-xl bg-black/40 border border-white/10 px-4 py-3 text-sm"
      />
      {field.help && <span className="block text-[11px] text-white/35 mt-1">{field.help}</span>}
    </label>
  );
}
