'use client';

import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { StatusDot } from '@/components/ui/StatusDot';
import { Skeleton } from '@/components/ui/Skeleton';
import { useIncidents } from '@/hooks/queries/useAlerts';
import { useState } from 'react';

type Tab = 'alerts' | 'incidents' | 'maintenance';

export default function AlertsPage() {
  const [activeTab, setActiveTab] = useState<Tab>('alerts');
  const { data: incidents, isLoading } = useIncidents();

  const tabs: { key: Tab; label: string }[] = [
    { key: 'alerts', label: 'Alerts' },
    { key: 'incidents', label: 'Incidents' },
    { key: 'maintenance', label: 'Maintenance' },
  ];

  const openIncidents = incidents?.filter((i) => i.status === 'open') ?? [];

  return (
    <div>
      <PageHeader
        title="Alerts"
        description="Active alerts, incidents, and maintenance windows"
      />

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-white/[0.06] mb-6">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-4 py-2 text-sm font-medium transition-colors ${
              activeTab === tab.key
                ? 'text-sky-400 border-b-2 border-sky-400'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === 'alerts' && (
        <div className="space-y-3">
          {isLoading &&
            Array.from({ length: 4 }).map((_, i) => (
              <GlassCard key={i} className="p-4">
                <div className="flex items-center gap-3">
                  <Skeleton className="h-5 w-5 rounded-full" />
                  <Skeleton className="h-5 w-64" />
                  <Skeleton className="h-5 w-16 ml-auto" />
                </div>
              </GlassCard>
            ))}
          {openIncidents.map((inc) => (
            <GlassCard key={inc.id} className="p-4">
              <div className="flex items-center gap-3">
                <StatusDot
                  status={inc.severity === 'critical' ? 'offline' : inc.severity === 'warning' ? 'maintenance' : 'unknown'}
                  pulse={inc.severity === 'critical'}
                />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-slate-200 truncate">{inc.title}</p>
                  <p className="text-xs text-slate-500">{new Date(inc.created_at).toLocaleString()}</p>
                </div>
                <Badge variant="severity" severity={inc.severity}>{inc.severity}</Badge>
              </div>
            </GlassCard>
          ))}
          {!isLoading && openIncidents.length === 0 && (
            <GlassCard className="p-8">
              <p className="text-center text-sm text-slate-500">No active alerts</p>
            </GlassCard>
          )}
        </div>
      )}

      {activeTab === 'incidents' && (
        <div className="space-y-3">
          {isLoading &&
            Array.from({ length: 4 }).map((_, i) => (
              <GlassCard key={i} className="p-4">
                <Skeleton className="h-5 w-full" />
              </GlassCard>
            ))}
          {incidents?.map((inc) => (
            <GlassCard key={inc.id} className="p-4">
              <div className="flex items-center gap-3">
                <StatusDot
                  status={
                    inc.status === 'resolved' ? 'online' :
                    inc.status === 'acknowledged' ? 'maintenance' : 'offline'
                  }
                />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-slate-200 truncate">{inc.title}</p>
                  <p className="text-xs text-slate-500">
                    {inc.rule} &middot; {new Date(inc.created_at).toLocaleString()}
                  </p>
                </div>
                <Badge variant="severity" severity={inc.severity}>{inc.severity}</Badge>
                <Badge>{inc.status}</Badge>
              </div>
            </GlassCard>
          ))}
          {!isLoading && (!incidents || incidents.length === 0) && (
            <GlassCard className="p-8">
              <p className="text-center text-sm text-slate-500">No incidents</p>
            </GlassCard>
          )}
        </div>
      )}

      {activeTab === 'maintenance' && (
        <GlassCard className="p-8">
          <p className="text-center text-sm text-slate-500">No maintenance windows configured</p>
        </GlassCard>
      )}
    </div>
  );
}
