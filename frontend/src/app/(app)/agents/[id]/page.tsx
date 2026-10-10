'use client';

import { useParams } from 'next/navigation';
import { PageHeader } from '@/components/layout/PageHeader';
import { Breadcrumbs } from '@/components/layout/Breadcrumbs';
import { GlassCard } from '@/components/ui/GlassCard';
import { StatusDot } from '@/components/ui/StatusDot';
import { Badge } from '@/components/ui/Badge';
import { Skeleton } from '@/components/ui/Skeleton';
import { Button } from '@/components/ui/Button';
import { EChart } from '@/components/charts/LazyEChart';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { get, patch, post, put, apiErrorMessage } from '@/lib/api';
import { cn, formatUptime, severityColor } from '@/lib/utils';
import { MAX_WATCHED_SERVICES, parseServiceList, serviceBadge } from '@/lib/agentServices';
import { useToastStore } from '@/stores/toast';
import { useIsEditor } from '@/stores/auth';
import { ArrowLeft, Monitor, Cpu, HardDrive, MemoryStick, FileText, Save, Trash2, Activity, Pencil, X, type LucideIcon } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import type { Agent, AgentServiceState, AgentSnapshot } from '@/types';

interface AgentDetail extends Agent {
  snapshots: AgentSnapshot[];
  log_levels?: string;
  log_channels?: string;
  log_file_paths?: string;
  agent_log_level?: string;
  watched_services?: string[];
  services?: AgentServiceState[];
  services_reported_at?: string | null;
}

export default function AgentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const agentId = Number(id);

  const { data, isLoading } = useQuery({
    queryKey: ['agent', agentId],
    queryFn: () => get<AgentDetail>(`/api/v1/agents/${agentId}`),
    enabled: agentId > 0,
    refetchInterval: 15_000,
  });

  const toast = useToastStore((s) => s.show);
  const router = useRouter();
  const [uninstalling, setUninstalling] = useState(false);

  const online = data?.last_seen
    ? Date.now() - new Date(data.last_seen).getTime() < 120_000
    : false;

  const latest = data?.snapshots?.[0];

  async function handleUninstall() {
    if (!confirm(`Uninstall the agent on "${data?.name}"?\n\nThis will stop the agent process, remove the scheduled task/service, delete all files on the remote machine, and remove the agent from Nodeglow.`)) return;
    setUninstalling(true);
    try {
      await post(`/api/v1/agents/${agentId}/uninstall`);
      toast('Uninstall command sent — agent will uninstall on next check-in', 'success');
      router.push('/agents');
    } catch {
      toast('Failed to send uninstall command', 'error');
    } finally {
      setUninstalling(false);
    }
  }

  return (
    <div>
      <Breadcrumbs items={[{ label: 'Agents', href: '/agents' }, { label: data?.name ?? `Agent #${agentId}` }]} />
      <PageHeader
        title={data?.name ?? 'Agent'}
        description={data?.hostname ?? ''}
        actions={
          <div className="flex items-center gap-2">
            <Button variant="danger" size="sm" onClick={handleUninstall} disabled={uninstalling || !online}>
              <Trash2 size={14} />
              {uninstalling ? 'Sending...' : 'Uninstall'}
            </Button>
            <Link href="/agents">
              <Button variant="ghost" size="sm"><ArrowLeft size={16} /> Back</Button>
            </Link>
          </div>
        }
      />

      {/* Status Header */}
      <GlassCard className="p-4 mb-6">
        {isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : data ? (
          <div className="flex items-center gap-6 flex-wrap">
            <div className="flex items-center gap-3">
              <StatusDot status={online ? 'online' : 'offline'} pulse={!online} />
              <span className="text-sm text-slate-300">{online ? 'Online' : 'Offline'}</span>
            </div>
            {data.platform && <Badge>{data.platform}</Badge>}
            {data.arch && <Badge>{data.arch}</Badge>}
            {data.agent_version && <Badge>v{data.agent_version}</Badge>}
            {data.last_seen && (
              <span className="text-xs text-slate-500">
                Last seen: {new Date(data.last_seen).toLocaleString()}
              </span>
            )}
            {latest?.uptime_s != null && (
              <span className="text-xs text-slate-500">
                Uptime: {formatUptime(latest.uptime_s)}
              </span>
            )}
          </div>
        ) : null}
      </GlassCard>

      {/* Metrics Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
        <MetricCard
          icon={Cpu}
          label="CPU"
          value={latest?.cpu_pct}
          suffix="%"
          loading={isLoading}
        />
        <MetricCard
          icon={MemoryStick}
          label="Memory"
          value={latest?.mem_pct}
          suffix="%"
          extra={latest ? `${((latest.mem_used_mb ?? 0) / 1024).toFixed(1)} / ${((latest.mem_total_mb ?? 0) / 1024).toFixed(1)} GB` : undefined}
          loading={isLoading}
        />
        <MetricCard
          icon={HardDrive}
          label="Disk"
          value={latest?.disk_pct}
          suffix="%"
          loading={isLoading}
        />
      </div>

      {/* Watched Services */}
      {data && <WatchedServices agentId={agentId} data={data} online={online} />}

      {/* Log Settings */}
      {data && <LogSettings agentId={agentId} data={data} />}

      {/* CPU/Memory Chart */}
      <GlassCard className="p-4 mb-6">
        <h3 className="text-sm font-medium text-slate-300 mb-3 flex items-center gap-2">
          <Monitor size={16} className="text-sky-400" /> Performance History
        </h3>
        {isLoading || !data?.snapshots?.length ? (
          <Skeleton className="h-[250px] w-full" />
        ) : (
          <EChart
            height={250}
            option={{
              tooltip: { trigger: 'axis' },
              legend: { data: ['CPU', 'Memory', 'Disk'] },
              xAxis: {
                type: 'category',
                data: data.snapshots.map((s) =>
                  new Date(s.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
                ).reverse(),
              },
              yAxis: { type: 'value', max: 100, axisLabel: { formatter: '{value}%' } },
              series: [
                {
                  name: 'CPU',
                  type: 'line',
                  data: data.snapshots.map((s) => s.cpu_pct).reverse(),
                  color: '#38BDF8',
                  smooth: true,
                  areaStyle: { opacity: 0.08 },
                },
                {
                  name: 'Memory',
                  type: 'line',
                  data: data.snapshots.map((s) => s.mem_pct).reverse(),
                  color: '#A78BFA',
                  smooth: true,
                  areaStyle: { opacity: 0.08 },
                },
                {
                  name: 'Disk',
                  type: 'line',
                  data: data.snapshots.map((s) => s.disk_pct).reverse(),
                  color: '#34D399',
                  smooth: true,
                  areaStyle: { opacity: 0.08 },
                },
              ],
            }}
          />
        )}
      </GlassCard>
    </div>
  );
}

function WatchedServices({ agentId, data, online }: { agentId: number; data: AgentDetail; online: boolean }) {
  const toast = useToastStore((s) => s.show);
  const qc = useQueryClient();
  const canEdit = useIsEditor();
  const isWindows = data.platform?.toLowerCase().includes('windows');
  const watched = data.watched_services ?? [];
  const services = data.services ?? [];

  const [editing, setEditing] = useState(false);
  const [text, setText] = useState('');
  const [saving, setSaving] = useState(false);
  const parsed = parseServiceList(text);
  const tooMany = parsed.names.length > MAX_WATCHED_SERVICES;

  function startEdit() {
    setText(watched.join('\n'));
    setEditing(true);
  }

  async function handleSave() {
    if (parsed.invalid.length || tooMany) return;
    setSaving(true);
    try {
      await put(`/api/v1/agents/${agentId}/services`, { services: parsed.names });
      qc.invalidateQueries({ queryKey: ['agent', agentId] });
      toast('Watched services saved', 'success');
      setEditing(false);
    } catch (e) {
      toast(apiErrorMessage(e, 'Failed to save watched services'), 'error');
    } finally {
      setSaving(false);
    }
  }

  const neverReported = watched.length > 0 && !data.services_reported_at;

  return (
    <GlassCard className="p-4 mb-6">
      <div className="flex items-center justify-between gap-2 mb-4">
        <h3 className="text-sm font-medium text-slate-300 flex items-center gap-2">
          <Activity size={16} className="text-sky-400" /> Watched Services
        </h3>
        {canEdit && !editing && (
          <Button size="sm" variant="ghost" onClick={startEdit}>
            <Pencil size={14} /> Edit
          </Button>
        )}
      </div>

      {editing ? (
        <div className="space-y-3">
          <p className="text-xs text-slate-500">
            {isWindows
              ? 'One Windows service name per line (the service name, not the display name — e.g. Spooler, W32Time).'
              : 'One systemd unit per line (e.g. nginx, sshd, docker.service).'}
          </p>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={5}
            placeholder={isWindows ? 'Spooler\nW32Time' : 'nginx\nsshd'}
            className="ng-input font-mono text-xs"
            aria-label="Watched services"
          />
          {parsed.invalid.length > 0 && (
            <p className="text-xs text-red-400">Invalid name(s): {parsed.invalid.join(', ')}</p>
          )}
          {tooMany && (
            <p className="text-xs text-red-400">At most {MAX_WATCHED_SERVICES} services can be watched.</p>
          )}
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)} disabled={saving}>
              <X size={14} /> Cancel
            </Button>
            <Button size="sm" onClick={handleSave} disabled={saving || parsed.invalid.length > 0 || tooMany}>
              <Save size={14} />
              {saving ? 'Saving...' : 'Save'}
            </Button>
          </div>
        </div>
      ) : watched.length === 0 ? (
        <p className="text-xs text-slate-500">
          No services watched.{canEdit ? ' Add services to get an incident when one stops running.' : ''}
        </p>
      ) : (
        <>
          {neverReported && (
            <p className="text-xs text-amber-400 mb-3">
              The agent has not reported service states yet. It picks up the list on its next check-in;
              agents older than this feature need an update first.
            </p>
          )}
          {!neverReported && !online && (
            <p className="text-xs text-slate-500 mb-3">Agent is offline — states below are from its last report.</p>
          )}
          <ul className="divide-y divide-white/[0.06]">
            {services.map((s) => {
              const badge = serviceBadge(s.state);
              return (
                <li key={s.name} className="flex items-center justify-between gap-3 py-2">
                  <div className="min-w-0">
                    <p className="text-sm text-slate-200 font-mono truncate">{s.name}</p>
                    <p className="text-[11px] text-slate-500">
                      {s.start_type ? `Start: ${s.start_type}` : ''}
                      {s.start_type && s.since ? ' · ' : ''}
                      {s.since ? `since ${new Date(s.since).toLocaleString()}` : ''}
                    </p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {s.alerted && <Badge variant="severity" severity="critical">Incident</Badge>}
                    <span
                      className={cn(
                        'inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border',
                        badge.severity === 'ok'
                          ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
                          : badge.severity === 'none'
                            ? 'bg-white/[0.06] text-slate-400 border-white/[0.08]'
                            : severityColor(badge.severity),
                      )}
                    >
                      {badge.label}
                    </span>
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </GlassCard>
  );
}

const WINDOWS_CHANNELS = [
  { key: 'System', label: 'System' },
  { key: 'Application', label: 'Application' },
  { key: 'Security', label: 'Security' },
  { key: 'Setup', label: 'Setup' },
  { key: 'Microsoft-Windows-PowerShell/Operational', label: 'PowerShell' },
  { key: 'Microsoft-Windows-Windows Defender/Operational', label: 'Defender' },
  { key: 'Microsoft-Windows-TaskScheduler/Operational', label: 'Task Scheduler' },
  { key: 'Microsoft-Windows-TerminalServices-LocalSessionManager/Operational', label: 'RDP Sessions' },
  { key: 'Microsoft-Windows-Sysmon/Operational', label: 'Sysmon' },
  { key: 'Microsoft-Windows-WindowsUpdateClient/Operational', label: 'Windows Update' },
];

const SEVERITY_LEVELS = [
  { value: 1, label: 'Critical' },
  { value: 2, label: 'Error' },
  { value: 3, label: 'Warning' },
  { value: 4, label: 'Info' },
  { value: 5, label: 'Verbose' },
];

function LogSettings({ agentId, data }: { agentId: number; data: AgentDetail }) {
  const toast = useToastStore((s) => s.show);
  const qc = useQueryClient();
  const isWindows = data.platform?.toLowerCase().includes('windows');

  const [agentLogLevel, setAgentLogLevel] = useState(data.agent_log_level ?? 'errors');
  const [logLevels, setLogLevels] = useState<Set<number>>(() => {
    const csv = data.log_levels ?? '1,2,3';
    return new Set(csv.split(',').filter(Boolean).map(Number));
  });
  const [logChannels, setLogChannels] = useState<Set<string>>(() => {
    const csv = data.log_channels ?? 'System,Application';
    return new Set(csv.split(',').filter(Boolean));
  });
  const [logFilePaths, setLogFilePaths] = useState(data.log_file_paths ?? '');
  const [saving, setSaving] = useState(false);

  // Sync when data changes from server
  useEffect(() => {
    setAgentLogLevel(data.agent_log_level ?? 'errors');
    setLogLevels(new Set((data.log_levels ?? '1,2,3').split(',').filter(Boolean).map(Number)));
    setLogChannels(new Set((data.log_channels ?? 'System,Application').split(',').filter(Boolean)));
    setLogFilePaths(data.log_file_paths ?? '');
  }, [data.agent_log_level, data.log_levels, data.log_channels, data.log_file_paths]);

  const toggleLevel = (lvl: number) => {
    setLogLevels((prev) => {
      const next = new Set(prev);
      if (next.has(lvl)) next.delete(lvl); else next.add(lvl);
      return next;
    });
  };

  const toggleChannel = (ch: string) => {
    setLogChannels((prev) => {
      const next = new Set(prev);
      if (next.has(ch)) next.delete(ch); else next.add(ch);
      return next;
    });
  };

  async function handleSave() {
    setSaving(true);
    try {
      await patch(`/api/v1/agents/${agentId}`, {
        agent_log_level: agentLogLevel,
        log_levels: Array.from(logLevels).sort().join(','),
        log_channels: Array.from(logChannels).join(','),
        log_file_paths: logFilePaths,
      });
      qc.invalidateQueries({ queryKey: ['agent', agentId] });
      toast('Log settings saved', 'success');
    } catch {
      toast('Failed to save settings', 'error');
    } finally {
      setSaving(false);
    }
  }

  return (
    <GlassCard className="p-4 mb-6">
      <h3 className="text-sm font-medium text-slate-300 mb-4 flex items-center gap-2">
        <FileText size={16} className="text-sky-400" /> Log Collection Settings
      </h3>

      <div className="space-y-5">
        {/* Agent Log Level */}
        <div>
          <label className="ng-label">Agent Log Level</label>
          <p className="text-xs text-slate-500 mb-2">Controls which of the agent&apos;s own logs are uploaded</p>
          <div className="flex gap-2">
            {(['off', 'errors', 'all'] as const).map((lvl) => (
              <button
                key={lvl}
                onClick={() => setAgentLogLevel(lvl)}
                className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                  agentLogLevel === lvl
                    ? 'accent-bg text-white'
                    : 'bg-white/[0.04] text-slate-400 border border-white/[0.06] hover:bg-white/[0.08]'
                }`}
              >
                {lvl === 'off' ? 'Off' : lvl === 'errors' ? 'Errors only' : 'All'}
              </button>
            ))}
          </div>
        </div>

        {/* Windows-specific: Event Log Levels */}
        {isWindows && (
          <div>
            <label className="ng-label">Event Log Severity Levels</label>
            <p className="text-xs text-slate-500 mb-2">Which Windows Event Log severity levels to collect</p>
            <div className="flex flex-wrap gap-2">
              {SEVERITY_LEVELS.map((s) => (
                <button
                  key={s.value}
                  onClick={() => toggleLevel(s.value)}
                  className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                    logLevels.has(s.value)
                      ? 'accent-bg text-white'
                      : 'bg-white/[0.04] text-slate-400 border border-white/[0.06] hover:bg-white/[0.08]'
                  }`}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Windows-specific: Event Log Channels */}
        {isWindows && (
          <div>
            <label className="ng-label">Event Log Channels</label>
            <p className="text-xs text-slate-500 mb-2">Which Windows Event Log channels to monitor</p>
            <div className="flex flex-wrap gap-2">
              {WINDOWS_CHANNELS.map((ch) => (
                <button
                  key={ch.key}
                  onClick={() => toggleChannel(ch.key)}
                  className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                    logChannels.has(ch.key)
                      ? 'accent-bg text-white'
                      : 'bg-white/[0.04] text-slate-400 border border-white/[0.06] hover:bg-white/[0.08]'
                  }`}
                >
                  {ch.label}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Custom Log File Paths */}
        <div>
          <label className="ng-label">Custom Log File Paths</label>
          <p className="text-xs text-slate-500 mb-2">
            {isWindows
              ? 'One file path per line (e.g. C:\\Logs\\app.log)'
              : 'One file path per line, glob patterns supported (e.g. /var/log/auth.log)'}
          </p>
          <textarea
            value={logFilePaths}
            onChange={(e) => setLogFilePaths(e.target.value)}
            rows={3}
            placeholder={isWindows ? 'C:\\Logs\\app.log' : '/var/log/auth.log\n/var/log/syslog'}
            className="ng-input font-mono text-xs"
          />
        </div>

        {/* Save button */}
        <div className="flex justify-end">
          <Button size="sm" onClick={handleSave} disabled={saving}>
            <Save size={14} />
            {saving ? 'Saving...' : 'Save Settings'}
          </Button>
        </div>
      </div>
    </GlassCard>
  );
}

function MetricCard({
  icon: Icon,
  label,
  value,
  suffix,
  extra,
  loading,
}: {
  icon: LucideIcon;
  label: string;
  value?: number | null;
  suffix?: string;
  extra?: string;
  loading: boolean;
}) {
  const pct = value ?? 0;
  const color = pct >= 90 ? 'text-red-400' : pct >= 75 ? 'text-amber-400' : 'text-emerald-400';
  const barColor = pct >= 90 ? '#F87171' : pct >= 75 ? '#FBBF24' : '#34D399';

  return (
    <GlassCard className="p-4">
      <div className="flex items-center gap-2 mb-2">
        <Icon size={16} className="text-slate-400" />
        <span className="text-xs text-slate-500 uppercase tracking-wider">{label}</span>
      </div>
      {loading ? (
        <Skeleton className="h-8 w-20" />
      ) : (
        <>
          <p className={`text-3xl font-bold ${color}`}>
            {value != null ? Math.round(value) : '—'}{value != null ? suffix : ''}
          </p>
          {extra && <p className="text-[10px] text-slate-500 mt-1">{extra}</p>}
          <div className="h-1.5 rounded-full bg-white/[0.06] mt-2 overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-500"
              style={{ width: `${Math.min(pct, 100)}%`, background: barColor }}
            />
          </div>
        </>
      )}
    </GlassCard>
  );
}
