'use client';

import { useEffect, useState } from 'react';
import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { useAudit } from '@/hooks/queries/useAudit';
import {
  Shield, ChevronLeft, ChevronRight, Filter,
  LogIn, LogOut, Settings, Server, AlertTriangle,
  Download, Upload, Wrench,
} from 'lucide-react';

const ACTION_ICONS: Record<string, React.ElementType> = {
  'auth.login': LogIn,
  'auth.logout': LogOut,
  'settings.update': Settings,
  'host.create': Server,
  'host.delete': Server,
  'incident.acknowledge': AlertTriangle,
  'incident.resolve': AlertTriangle,
  'backup.export': Download,
  'backup.restore': Upload,
  'maintenance.toggle': Wrench,
};

const ACTION_COLORS: Record<string, string> = {
  'auth.login': 'text-emerald-400',
  'auth.logout': 'text-slate-400',
  'settings.update': 'text-sky-400',
  'host.create': 'text-emerald-400',
  'host.delete': 'text-red-400',
  'incident.acknowledge': 'text-amber-400',
  'incident.resolve': 'text-emerald-400',
  'backup.export': 'text-violet-400',
  'backup.restore': 'text-orange-400',
  'maintenance.toggle': 'text-amber-400',
};

const PAGE_SIZE = 50;

export default function AuditLogPage() {
  useEffect(() => { document.title = 'Audit Log | Nodeglow'; }, []);

  const [page, setPage] = useState(0);
  const [filterAction, setFilterAction] = useState('');
  const [filterUser, setFilterUser] = useState('');

  const { data, isLoading } = useAudit({
    action: filterAction || undefined,
    user: filterUser || undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  });

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 0;

  function formatTime(ts: string | null) {
    if (!ts) return '-';
    const d = new Date(ts);
    return d.toLocaleString();
  }

  return (
    <div>
      <PageHeader title="Audit Log" description="Track all user actions across the system" />

      {/* Filters */}
      <GlassCard className="p-4 mb-4">
        <div className="flex items-center gap-3 flex-wrap">
          <Filter size={14} className="text-slate-400" />
          <select
            value={filterAction}
            onChange={(e) => { setFilterAction(e.target.value); setPage(0); }}
            className="ng-input w-48"
          >
            <option value="">All Actions</option>
            <option value="auth.login">Login</option>
            <option value="auth.logout">Logout</option>
            <option value="settings.update">Settings Update</option>
            <option value="host.create">Host Create</option>
            <option value="host.delete">Host Delete</option>
            <option value="incident.acknowledge">Incident Acknowledge</option>
            <option value="incident.resolve">Incident Resolve</option>
            <option value="backup.export">Backup Export</option>
            <option value="backup.restore">Backup Restore</option>
            <option value="maintenance.toggle">Maintenance Toggle</option>
          </select>
          <input
            type="text"
            placeholder="Filter by username..."
            value={filterUser}
            onChange={(e) => { setFilterUser(e.target.value); setPage(0); }}
            className="ng-input w-48"
          />
          {data && (
            <span className="text-xs text-slate-500 ml-auto">
              {data.total} total entries
            </span>
          )}
        </div>
      </GlassCard>

      {/* Table */}
      <GlassCard className="overflow-hidden">
        {isLoading ? (
          <div className="p-4 space-y-3">
            {Array.from({ length: 10 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
          </div>
        ) : !data?.logs.length ? (
          <div className="flex flex-col items-center justify-center py-16 text-slate-500">
            <Shield size={48} className="mb-3 opacity-30" />
            <p>No audit log entries found</p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/[0.06]">
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-400 uppercase">Time</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-400 uppercase">User</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-400 uppercase">Action</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-400 uppercase">Target</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-400 uppercase">IP</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-400 uppercase">Details</th>
              </tr>
            </thead>
            <tbody>
              {data.logs.map((entry) => {
                const Icon = ACTION_ICONS[entry.action] ?? Shield;
                const color = ACTION_COLORS[entry.action] ?? 'text-slate-400';
                return (
                  <tr key={entry.id} className="border-b border-white/[0.04] hover:bg-white/[0.02] transition-colors">
                    <td className="px-4 py-2.5 text-xs text-slate-400 whitespace-nowrap font-mono">
                      {formatTime(entry.timestamp)}
                    </td>
                    <td className="px-4 py-2.5">
                      <span className="text-sm" style={{ color: 'var(--ng-text-primary)' }}>
                        {entry.username ?? '-'}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-2">
                        <Icon size={14} className={color} />
                        <Badge>{entry.action}</Badge>
                      </div>
                    </td>
                    <td className="px-4 py-2.5 text-sm text-slate-300">
                      {entry.target_name ? (
                        <span>
                          {entry.target_type && <span className="text-slate-500">{entry.target_type}/</span>}
                          {entry.target_name}
                        </span>
                      ) : entry.target_type ? (
                        <span className="text-slate-500">{entry.target_type}</span>
                      ) : '-'}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-slate-500 font-mono">
                      {entry.ip_address ?? '-'}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-slate-500 max-w-[200px] truncate">
                      {entry.details ? JSON.stringify(entry.details) : '-'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between px-4 py-3 border-t border-white/[0.06]">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setPage(Math.max(0, page - 1))}
              disabled={page === 0}
            >
              <ChevronLeft size={14} /> Previous
            </Button>
            <span className="text-xs text-slate-400">
              Page {page + 1} of {totalPages}
            </span>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setPage(Math.min(totalPages - 1, page + 1))}
              disabled={page >= totalPages - 1}
            >
              Next <ChevronRight size={14} />
            </Button>
          </div>
        )}
      </GlassCard>
    </div>
  );
}
