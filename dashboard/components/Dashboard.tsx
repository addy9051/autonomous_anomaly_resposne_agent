'use client';

import React, { useEffect, useState, useCallback } from 'react';
import Link from 'next/link';
import { LayoutDashboard, Activity, Database, Zap, Shield, Play, Wifi, WifiOff, AlertCircle } from 'lucide-react';
import StatusCards from './StatusCards';
import TelemetryPulse from './TelemetryPulse';
import ActiveIncidents from './ActiveIncidents';
import { 
  fetchStats, 
  fetchRecentTelemetry, 
  fetchActiveIncidents, 
  fetchDetailedHealth,
  triggerDemo,
  TelemetryEvent,
  IncidentRecord,
  InferenceReadiness
} from '@/lib/api';

export default function Dashboard() {
  const [stats, setStats] = useState<any>(null);
  const [telemetry, setTelemetry] = useState<TelemetryEvent[]>([]);
  const [activeIncidents, setActiveIncidents] = useState<IncidentRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [isInjecting, setIsInjecting] = useState(false);
  const [isBackendOnline, setIsBackendOnline] = useState<boolean | null>(null);
  const [inference, setInference] = useState<InferenceReadiness | null>(null);
  const [errorCount, setErrorCount] = useState(0);
  const isInferenceReady = inference?.chat === 'ready' && inference?.embeddings === 'ready';

  const refreshData = useCallback(async () => {
    try {
      const [s, t, a] = await Promise.all([
        fetchStats(),
        fetchRecentTelemetry(),
        fetchActiveIncidents()
      ]);
      setStats(s);
      setTelemetry(t);
      setActiveIncidents(a);
      setLoading(false);
      setErrorCount(0);
    } catch (err) {
      console.error("Data refresh failed:", err);
      setIsBackendOnline(false);
      setErrorCount(prev => prev + 1);
    }
  }, []);

  const refreshHealth = useCallback(async () => {
    try {
      const health = await fetchDetailedHealth();
      setIsBackendOnline(health?.status === 'healthy');
      setInference(health?.inference ?? null);
    } catch (err) {
      console.error("Health refresh failed:", err);
      setIsBackendOnline(false);
      setInference(null);
    }
  }, []);

  useEffect(() => {
    refreshData();
    refreshHealth();
    const interval = setInterval(refreshData, 3000);
    const healthInterval = setInterval(refreshHealth, 5000);
    return () => {
      clearInterval(interval);
      clearInterval(healthInterval);
    };
  }, [refreshData, refreshHealth]);

  const handleTriggerDemo = async () => {
    setIsInjecting(true);
    try {
      await triggerDemo();
      console.log("Demo successfully triggered");
      // Immediate refresh to show result
      setTimeout(refreshData, 500);
    } catch (err) {
      console.error("Demo failed:", err);
      alert("⚠️ Request Blocked: Please check browser console for CORS or Network errors.");
    } finally {
      setTimeout(() => setIsInjecting(false), 2000);
    }
  };

  return (
    <div className="flex flex-col min-h-screen bg-background text-foreground selection:bg-primary selection:text-black">
      {/* Navigation */}
      <nav className="glass sticky top-0 z-50 px-8 py-4 flex items-center justify-between border-b border-white/5 mx-6 mt-6 rounded-3xl">
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-primary rounded-2xl flex items-center justify-center shadow-neon">
              <Zap className="text-black" size={24} />
            </div>
            <div className="flex flex-col">
              <h1 className="text-lg font-black tracking-tight uppercase leading-none">SRE MISSION CONTROL</h1>
              <span className="text-[10px] font-bold text-primary tracking-[0.2em] uppercase">Autonomous Response v3</span>
            </div>
          </div>

          {/* Connection Status Indicator */}
          <div className={`flex items-center gap-2 px-3 py-1 rounded-full text-[10px] font-bold border transition-all ${
            isBackendOnline === true && isInferenceReady
              ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20' 
              : isBackendOnline === false || inference?.chat === 'unavailable' || inference?.embeddings === 'unavailable'
                ? 'bg-rose-500/10 text-rose-500 border-rose-500/20 animate-pulse'
                : 'bg-white/5 text-white/40 border-white/10'
          }`}>
              {isBackendOnline === true && isInferenceReady ? <Wifi size={12} /> : <WifiOff size={12} />}
              {isBackendOnline === false
                ? 'API CONNECTION FAILED'
                : inference?.chat === 'unavailable' || inference?.embeddings === 'unavailable'
                  ? 'MODEL CONNECTION FAILED'
                  : isBackendOnline === true && isInferenceReady
                    ? 'SYSTEM CONNECTED'
                    : 'STARTING INFERENCE'}
            </div>
        </div>
        
        <div className="hidden lg:flex items-center gap-8 ml-12">
           <NavLink icon={<LayoutDashboard size={18} />} label="Overview" active />
           <Link href="/connectors">
             <NavLink icon={<Database size={18} />} label="Connectors" />
           </Link>
           <NavLink icon={<Activity size={18} />} label="Observability" />
           <NavLink icon={<Database size={18} />} label="Knowledge Base" />
           <NavLink icon={<Shield size={18} />} label="Security" />
        </div>

        <button 
          onClick={handleTriggerDemo}
          disabled={isInjecting || !isBackendOnline || !isInferenceReady}
          className={`flex items-center gap-2 font-bold text-xs uppercase tracking-widest px-6 py-3 rounded-2xl transition-all active:scale-95 disabled:opacity-50 ${
            isBackendOnline && isInferenceReady
              ? 'bg-white text-black hover:bg-primary' 
              : 'bg-white/5 text-white/20'
          }`}
        >
          <Play size={14} fill="currentColor" />
          {isInjecting ? "Injecting..." : !isInferenceReady ? "Warming up models..." : "Synthesize Anomaly"}
        </button>
      </nav>

      {/* Main Content */}
      <main className="p-8 pb-16 space-y-8 animate-in fade-in duration-1000">
        {isBackendOnline && !isInferenceReady && inference?.chat !== 'unavailable' && inference?.embeddings !== 'unavailable' && (
          <section
            className="glass mx-auto max-w-2xl rounded-2xl border border-primary/20 p-5"
            role="status"
            aria-live="polite"
          >
            <div className="flex items-start gap-3">
              <Activity size={18} className="mt-0.5 shrink-0 animate-pulse text-primary" />
              <div className="min-w-0 flex-1">
                <p className="text-xs font-black uppercase tracking-widest text-primary">
                  {inference ? 'Starting inference models' : 'Checking model readiness'}
                </p>
                <p className="mt-1 text-xs leading-relaxed text-foreground/60">
                  Modal is waking the GPU workers and loading model weights. The first start after an idle period can take a few minutes.
                  The demo button will enable when chat and embeddings are ready.
                </p>
                <div
                  className="mt-4 h-1.5 overflow-hidden rounded-full bg-white/10"
                  role="progressbar"
                  aria-label="Waiting for inference models to start"
                  aria-valuetext="Model startup is in progress"
                >
                  <div className="h-full w-1/3 rounded-full bg-primary shadow-neon animate-modal-loading" />
                </div>
                {inference && (
                  <div className="mt-3 flex gap-4 text-[10px] font-bold uppercase tracking-wider text-foreground/40">
                    <span>Chat: {inference.chat}</span>
                    <span>Embeddings: {inference.embeddings}</span>
                  </div>
                )}
              </div>
            </div>
          </section>
        )}

        {isBackendOnline && (inference?.chat === 'unavailable' || inference?.embeddings === 'unavailable') && (
          <div className="mx-auto max-w-2xl rounded-2xl border border-rose-500/20 bg-rose-500/10 p-4 text-xs text-rose-400" role="alert">
            An inference endpoint is unavailable. Check the Modal deployment, vLLM API key and model endpoint settings.
            {inference && <span className="ml-2">Chat: {inference.chat}; embeddings: {inference.embeddings}.</span>}
          </div>
        )}

        {errorCount > 3 && (
          <div className="bg-rose-500/10 border border-rose-500/20 p-4 rounded-2xl flex items-center gap-4 text-rose-500 max-w-2xl mx-auto">
            <AlertCircle size={24} />
            <div className="text-xs">
              <p className="font-black uppercase">Dashboard Sync Failure</p>
              <p className="opacity-70 font-bold mt-1">We are unable to reach the FastAPI orchestrator. Please ensure the backend server is running and CORS is configured.</p>
            </div>
          </div>
        )}

        <StatusCards 
          activeIncidents={activeIncidents.length}
          resolvedIncidents={stats?.resolved_incidents || 0}
          memoryDegraded={Boolean(stats?.episodic_memory_degraded)}
        />

        <div className="mb-8">
          <TelemetryPulse events={telemetry} />
        </div>

        <ActiveIncidents incidents={activeIncidents} />
      </main>

      {/* Footer */}
      <footer className="mt-auto p-8 border-t border-white/5 opacity-30 flex justify-between items-center text-[10px] font-bold uppercase tracking-widest">
         <span>Property of AMEX - Enterprise AI Reliability Division</span>
         <span>Node US-Central-1 • System Online</span>
      </footer>
    </div>
  );
}

function NavLink({ icon, label, active = false }: { icon: any, label: string, active?: boolean }) {
  return (
    <div className={`flex items-center gap-2 cursor-pointer transition-colors hover:text-primary ${active ? 'text-primary' : 'text-foreground/40'}`}>
      {icon}
      <span className="text-xs font-bold uppercase tracking-widest">{label}</span>
    </div>
  );
}
