'use client';

import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  AlertCircle, 
  Search, 
  Settings, 
  Play, 
  CheckCircle2, 
  ChevronRight,
  ChevronDown,
  Database,
  Cloud,
  ShieldAlert,
  Terminal
} from 'lucide-react';
import { IncidentRecord } from '@/lib/api';

const STATUS_STEPS = [
  { id: 'detected', label: 'Detected', icon: AlertCircle },
  { id: 'diagnosing', label: 'Diagnosing', icon: Search },
  { id: 'action_pending', label: 'Analysis', icon: Settings },
  { id: 'action_executing', label: 'Executing', icon: Play },
  { id: 'resolved', label: 'Resolved', icon: CheckCircle2 },
];

interface Props {
  incidents: IncidentRecord[];
}

export default function ActiveIncidents({ incidents }: Props) {
  if (incidents.length === 0) return null;

  return (
    <div className="space-y-4">
      <h2 className="text-xl font-bold tracking-tight mb-4">Active Response Workflows</h2>
      {incidents.map((inc) => (
        <IncidentCard key={inc.incident_id} incident={inc} />
      ))}
    </div>
  );
}

function IncidentCard({ incident }: { incident: IncidentRecord }) {
  const [isExpanded, setIsExpanded] = useState(false);
  
  // Find current step index
  const currentStepIndex = STATUS_STEPS.findIndex(s => s.id === incident.status.toLowerCase());
  const effectiveIndex = currentStepIndex === -1 ? 0 : currentStepIndex;

  return (
    <div className="glass rounded-3xl overflow-hidden backdrop-blur-3xl lg:p-8 p-6 transition-all duration-300 hover:border-primary/20">
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-6">
        
        {/* Incident Info */}
        <div className="flex-1">
          <div className="flex items-center gap-3 mb-2">
            <span className="text-xs font-mono text-primary/60 uppercase tracking-widest">ID: {incident.incident_id.substring(0, 8)}</span>
            <span className="glass-pill px-2 py-0.5 rounded-full text-[10px] uppercase font-bold text-danger animate-pulse-neon">
              {incident.anomaly_event?.severity || 'High'}
            </span>
          </div>
          <h3 className="text-lg font-bold mb-1">{incident.anomaly_event?.anomaly_type || 'Unknown Anomaly'}</h3>
          <p className="text-sm text-foreground/50 line-clamp-1">{incident.anomaly_event?.affected_services.join(', ')}</p>
        </div>

        {/* Railroad Map */}
        <div className="flex-2 w-full max-w-2xl px-4">
          <div className="flex items-center justify-between relative">
            {/* Connecting line background */}
            <div className="absolute top-1/2 left-0 w-full h-0.5 bg-foreground/5 -translate-y-1/2 -z-10" />
            
            {/* Active connecting line */}
            <motion.div 
               className="absolute top-1/2 left-0 h-0.5 bg-primary/40 -translate-y-1/2 -z-10"
               initial={{ width: 0 }}
               animate={{ width: `${(effectiveIndex / (STATUS_STEPS.length - 1)) * 100}%` }}
               transition={{ duration: 1 }}
            />

            {STATUS_STEPS.map((step, idx) => {
              const Icon = step.icon;
              const isActive = idx <= effectiveIndex;
              const isCurrent = idx === effectiveIndex;

              return (
                <div key={step.id} className="flex flex-col items-center gap-2">
                  <motion.div 
                    initial={false}
                    animate={{ 
                      scale: isCurrent ? 1.2 : 1,
                      backgroundColor: isActive ? 'var(--primary)' : 'var(--background)',
                      borderColor: isActive ? 'var(--primary)' : 'var(--card-border)',
                    }}
                    className={`w-10 h-10 rounded-full border-2 flex items-center justify-center transition-shadow ${isActive ? 'shadow-neon' : ''}`}
                  >
                    <Icon className={`w-5 h-5 ${isActive ? 'text-black' : 'text-foreground/30'}`} />
                  </motion.div>
                  <span className={`text-[10px] font-bold uppercase tracking-tighter ${isActive ? 'text-primary' : 'text-foreground/20'}`}>
                    {step.label}
                  </span>
                </div>
              );
            })}
          </div>
        </div>

        {/* Drill Down Toggle */}
        <button 
          onClick={() => setIsExpanded(!isExpanded)}
          className="p-3 rounded-full hover:bg-white/5 transition-colors self-center lg:self-auto"
        >
          {isExpanded ? <ChevronDown size={24} /> : <ChevronRight size={24} />}
        </button>
      </div>

      {/* Expanded Drill Down Section */}
      <AnimatePresence>
        {isExpanded && (
          <motion.div 
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden mt-8 border-t border-white/5 pt-8"
          >
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
              {/* Specialized Experts */}
              {incident.diagnosis_result?.sub_agent_reports && Object.entries(incident.diagnosis_result.sub_agent_reports).map(([name, report]: any) => (
                <ExpertNode key={name} name={name} report={report} />
              ))}
              
              {!incident.diagnosis_result?.sub_agent_reports && (
                  <div className="col-span-full flex items-center justify-center p-8 glass-pill rounded-3xl opacity-30 italic">
                      Specialist investigation in progress...
                  </div>
              )}
            </div>
            
            {/* Detailed Chain of Thought */}
            <div className="mt-8 p-6 glass-pill rounded-3xl bg-black/40">
                <div className="flex items-center gap-2 mb-3">
                   <Terminal size={16} className="text-secondary" />
                   <h4 className="text-xs uppercase font-bold tracking-widest text-secondary">Reasoning Chain</h4>
                </div>
                <p className="text-sm font-mono text-foreground/70 whitespace-pre-wrap leading-relaxed">
                    {incident.diagnosis_result?.reasoning_chain || 'Thinking... awaiting analysis reports.'}
                </p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function ExpertNode({ name, report }: { name: string, report: any }) {
  const icons: any = {
    database_expert: Database,
    network_expert: Cloud,
    security_auditor: ShieldAlert,
    application_expert: Terminal,
  };
  const Icon = icons[name] || Search;

  return (
    <div className="glass-pill p-4 rounded-2xl flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <Icon size={16} className="text-primary" />
        <span className="text-xs font-bold uppercase tracking-wider">{name.replace('_', ' ')}</span>
      </div>
      <p className="text-[11px] text-foreground/60 leading-normal line-clamp-3">
        {report.findings}
      </p>
      <div className="flex items-center justify-between mt-2">
        <span className="text-[10px] font-bold text-primary px-2 py-0.5 rounded-full bg-primary/10">
          CONF {Math.round(report.confidence * 100)}%
        </span>
      </div>
    </div>
  );
}
