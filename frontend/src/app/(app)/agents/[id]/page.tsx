'use client';

import { useParams } from 'next/navigation';
import { PageHeader } from '@/components/layout/PageHeader';
import { Breadcrumbs } from '@/components/layout/Breadcrumbs';
import { GlassCard } from '@/components/ui/GlassCard';
import { StatusDot } from '@/components/ui/StatusDot';
import { Badge } from '@/components/ui/Badge';
import { Skeleton } from '@/components/ui/Skeleton';
import { Button } from '@/components/ui/Button';
import { EChart } from '@/components/charts/EChart';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { get, patch } from '@/lib/api';
import { formatUptime } from '@/lib/utils';
import { useToastStore } from '@/stores/toast';
import { ArrowLeft, Monitor, Cpu, HardDrive, MemoryStick, FileText, Save } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useState } from 'react';
import type { Agent, AgentSnapshot } from '@/types';

interface AgentDetail extends Agent {
  snapshots: AgentSnapshot[];
  log_levels?: string;
  log_channels?: string;
  log_file_paths?: string;
  agent_log_level?: string;
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

  const online = data?.last_seen
    ? Date.now() - new Date(data.last_seen).getTime() < 120_000
    : false;

  const latest = data?.snapshots?.[0];

  return (
    <div>
      <Breadcrumbs items={[{ label: 'Agents', href: '/agents' }, { label: data?.name ?? `Agent #${agentId}` }]} />
      <PageHeader
        title={data?.name ?? 'Agent'}
        description={data?.hostname ?? ''}
        actions={
          <Link href="/agents">
            <Button variant="ghost" size="sm"><ArrowLeft size={16} /> Back</Button>
          </Link>
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
  icon: React.ElementType;
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
