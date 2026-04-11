'use client';

import React from 'react';
import { 
  LineChart, 
  Line, 
  XAxis, 
  YAxis, 
  CartesianGrid, 
  Tooltip, 
  Legend, 
  ResponsiveContainer,
  AreaChart,
  Area
} from 'recharts';
import { RewardHistoryEntry } from '@/lib/api';

interface Props {
  rewards: RewardHistoryEntry[];
}

export default function PolicyPerformance({ rewards }: Props) {
  // Process rewards for cumulative growth comparison
  // In a real A/B test, we'd have group labels. For now, let's split or just show history.
  // Assuming 'action' or a hidden metadata field would have the group.
  
  let controlSum = 0;
  let experimentalSum = 0;

  const data = rewards.map((r, idx) => {
    // Deterministic split if not provided in data
    const isExperimental = idx % 2 === 0; 
    if (isExperimental) experimentalSum += r.reward;
    else controlSum += r.reward;

    return {
      name: `Inc ${idx + 1}`,
      control: controlSum,
      experimental: experimentalSum,
      reward: r.reward,
    };
  });

  return (
    <div className="glass p-6 rounded-3xl h-[400px]">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-xl font-bold tracking-tight">RL Policy Performance (A/B)</h2>
        <div className="flex gap-4">
          <div className="flex items-center gap-2">
            <span className="w-3 h-3 rounded-full bg-secondary"></span>
            <span className="text-xs text-foreground/60 uppercase tracking-widest font-semibold">Control</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="w-3 h-3 rounded-full bg-accent"></span>
            <span className="text-xs text-foreground/60 uppercase tracking-widest font-semibold">Experimental</span>
          </div>
        </div>
      </div>
      
      <ResponsiveContainer width="100%" height="80%">
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="rgba(255,255,255,0.05)" />
          <XAxis 
            dataKey="name" 
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
          <Legend align="right" verticalAlign="top" height={36}/>
          <Line 
            type="monotone" 
            dataKey="control" 
            stroke="#3b82f6" 
            strokeWidth={3}
            dot={{ r: 4, fill: '#3b82f6', strokeWidth: 2, stroke: '#1a1a1a' }}
            activeDot={{ r: 6, strokeWidth: 0 }}
          />
          <Line 
            type="monotone" 
            dataKey="experimental" 
            stroke="#8b5cf6" 
            strokeWidth={3}
            dot={{ r: 4, fill: '#8b5cf6', strokeWidth: 2, stroke: '#1a1a1a' }}
            activeDot={{ r: 6, strokeWidth: 0 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
