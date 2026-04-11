'use client';

import React from 'react';
import { 
  AreaChart, 
  Area, 
  XAxis, 
  YAxis, 
  CartesianGrid, 
  Tooltip, 
  ResponsiveContainer 
} from 'recharts';
import { TelemetryEvent } from '@/lib/api';

interface PulseProps {
  events: TelemetryEvent[];
}

export default function TelemetryPulse({ events }: PulseProps) {
  // Extract and format data for the chart
  // We'll show event counts or specific values if it's a metric
  const data = events.slice(-30).map(e => ({
    time: new Date(e.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
    latency: e.payload?.p99_latency_ms || 0,
    errors: (e.payload?.error_rate || 0) * 100,
  }));

  return (
    <div className="glass p-6 rounded-3xl h-[400px]">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-xl font-bold tracking-tight">System Pulse</h2>
        <div className="flex gap-4">
          <div className="flex items-center gap-2">
            <span className="w-3 h-3 rounded-full bg-primary shadow-neon"></span>
            <span className="text-xs text-foreground/60 uppercase tracking-widest font-semibold">Latency</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="w-3 h-3 rounded-full bg-danger"></span>
            <span className="text-xs text-foreground/60 uppercase tracking-widest font-semibold">Error Rate</span>
          </div>
        </div>
      </div>
      
      <ResponsiveContainer width="100%" height="80%">
        <AreaChart data={data}>
          <defs>
            <linearGradient id="colorLatency" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#10b981" stopOpacity={0.3}/>
              <stop offset="95%" stopColor="#10b981" stopOpacity={0}/>
            </linearGradient>
            <linearGradient id="colorErrors" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#ef4444" stopOpacity={0.3}/>
              <stop offset="95%" stopColor="#ef4444" stopOpacity={0}/>
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="rgba(255,255,255,0.05)" />
          <XAxis 
            dataKey="time" 
            axisLine={false} 
            tickLine={false} 
            tick={{ fill: 'rgba(255,255,255,0.3)', fontSize: 10 }}
          />
          <YAxis 
            axisLine={false} 
            tickLine={false} 
            tick={{ fill: 'rgba(255,255,255,0.3)', fontSize: 10 }}
          />
          <Tooltip 
            contentStyle={{ backgroundColor: '#1a1a1a', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '12px' }}
            itemStyle={{ color: '#fff' }}
          />
          <Area 
            type="monotone" 
            dataKey="latency" 
            stroke="#10b981" 
            fillOpacity={1} 
            fill="url(#colorLatency)" 
            strokeWidth={2}
          />
          <Area 
            type="monotone" 
            dataKey="errors" 
            stroke="#ef4444" 
            fillOpacity={1} 
            fill="url(#colorErrors)" 
            strokeWidth={2}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
