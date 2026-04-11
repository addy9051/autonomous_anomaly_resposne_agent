'use client';

import React from 'react';
import { Activity, Shield, Cpu, Zap, Box } from 'lucide-react';

interface StatsProps {
  activeIncidents: number;
  resolvedIncidents: number;
  policyVersion: string;
}

export default function StatusCards({ activeIncidents, resolvedIncidents, policyVersion }: StatsProps) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
      <Card 
        title="Active Incidents" 
        value={activeIncidents.toString()} 
        icon={<Activity className={activeIncidents > 0 ? "text-danger animate-pulse" : "text-primary"} />}
        trend={activeIncidents > 0 ? "Critical Response" : "System Stable"}
        trendColor={activeIncidents > 0 ? "text-danger" : "text-primary"}
      />
      <Card 
        title="Autonomously Resolved" 
        value={resolvedIncidents.toString()} 
        icon={<Zap className="text-warning" />}
        trend="+12% from yesterday"
      />
      <Card 
        title="RL Policy Version" 
        value={policyVersion} 
        icon={<Shield className="text-accent" />}
        trend="v2.0.0-vw-stable"
      />
      <Card 
        title="Agent Uptime" 
        value="99.9%" 
        icon={<Box className="text-secondary" />}
        trend="4 Healthy Nodes"
      />
    </div>
  );
}

function Card({ title, value, icon, trend, trendColor = "text-foreground/50" }: any) {
  return (
    <div className="glass p-5 rounded-2xl relative overflow-hidden group">
      <div className="absolute top-0 right-0 p-4 opacity-20 group-hover:opacity-40 transition-opacity">
        {icon}
      </div>
      <h3 className="text-sm font-medium text-foreground/60 mb-1">{title}</h3>
      <div className="flex items-baseline gap-2">
        <span className="text-3xl font-bold tracking-tight">{value}</span>
      </div>
      <p className={`text-xs mt-2 ${trendColor} font-medium tracking-wide flex items-center gap-1`}>
        {trend}
      </p>
    </div>
  );
}
