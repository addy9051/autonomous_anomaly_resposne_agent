'use client';

import React, { useEffect, useState, useCallback } from 'react';
import { LayoutDashboard, Activity, Database, Zap, Shield, Play } from 'lucide-react';
import StatusCards from './StatusCards';
import TelemetryPulse from './TelemetryPulse';
import ActiveIncidents from './ActiveIncidents';
import PolicyPerformance from './PolicyPerformance';
import { 
  fetchStats, 
  fetchRecentTelemetry, 
  fetchActiveIncidents, 
  fetchResolvedIncidents, 
  fetchRewardHistory,
  triggerDemo,
  TelemetryEvent,
  IncidentRecord,
  RewardHistoryEntry
} from '@/lib/api';

export default function Dashboard() {
  const [stats, setStats] = useState<any>(null);
  const [telemetry, setTelemetry] = useState<TelemetryEvent[]>([]);
  const [activeIncidents, setActiveIncidents] = useState<IncidentRecord[]>([]);
  const [rewards, setRewards] = useState<RewardHistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [isInjecting, setIsInjecting] = useState(false);

  const refreshData = useCallback(async () => {
    try {
      const [s, t, a, r] = await Promise.all([
        fetchStats(),
        fetchRecentTelemetry(),
        fetchActiveIncidents(),
        fetchRewardHistory()
      ]);
      setStats(s);
      setTelemetry(t);
      setActiveIncidents(a);
      setRewards(r);
      setLoading(false);
    } catch (err) {
      console.error("Data refresh failed:", err);
    }
  }, []);

  useEffect(() => {
    refreshData();
    const interval = setInterval(refreshData, 3000);
    return () => clearInterval(interval);
  }, [refreshData]);

  const handleTriggerDemo = async () => {
    setIsInjecting(true);
    try {
      await triggerDemo();
      setTimeout(refreshData, 500);
    } catch (err) {
      console.error("Demo failed:", err);
    } finally {
      setTimeout(() => setIsInjecting(false), 2000);
    }
  };

  return (
    <div className="flex flex-col min-h-screen bg-background text-foreground selection:bg-primary selection:text-black">
      {/* Navigation */}
      <nav className="glass sticky top-0 z-50 px-8 py-4 flex items-center justify-between border-b border-white/5 mx-6 mt-6 rounded-3xl">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-primary rounded-2xl flex items-center justify-center shadow-neon">
            <Zap className="text-black" size={24} />
          </div>
          <div className="flex flex-col">
            <h1 className="text-lg font-black tracking-tight uppercase leading-none">SRE MISSION CONTROL</h1>
            <span className="text-[10px] font-bold text-primary tracking-[0.2em] uppercase">Autonomous Response v3</span>
          </div>
        </div>
        
        <div className="hidden lg:flex items-center gap-8 ml-12">
           <NavLink icon={<LayoutDashboard size={18} />} label="Overview" active />
           <NavLink icon={<Activity size={18} />} label="Observability" />
           <NavLink icon={<Database size={18} />} label="Knowledge Base" />
           <NavLink icon={<Shield size={18} />} label="Security" />
        </div>

        <button 
          onClick={handleTriggerDemo}
          disabled={isInjecting}
          className="flex items-center gap-2 bg-white text-black font-bold text-xs uppercase tracking-widest px-6 py-3 rounded-2xl hover:bg-primary transition-all active:scale-95 disabled:opacity-50"
        >
          <Play size={14} fill="currentColor" />
          {isInjecting ? "Injecting..." : "Synthesize Anomaly"}
        </button>
      </nav>

      {/* Main Content */}
      <main className="p-8 pb-16 space-y-8 animate-in fade-in duration-1000">
        <StatusCards 
          activeIncidents={activeIncidents.length}
          resolvedIncidents={stats?.resolved_incidents || 0}
          policyVersion={stats?.feedback_policy?.current_version || "v1.0"}
        />

        <div className="grid grid-cols-1 xl:grid-cols-2 gap-8">
          <TelemetryPulse events={telemetry} />
          <PolicyPerformance rewards={rewards} />
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
