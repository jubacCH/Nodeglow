'use client';

import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import { useEffect, useState, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Cpu, HardDrive, MemoryStick, Monitor, Pencil, Save, Trash2, X, type LucideIcon } from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { Breadcrumbs } from '@/components/layout/Breadcrumbs';
import { Card, CardHeader } from '@/components/ui/Card';
import { StatusPill } from '@/components/ui/StatusPill';
import { Badge } from '@/components/ui/Badge';
import { Skeleton } from '@/components/ui/Skeleton';
import { Button, buttonClasses } from '@/components/ui/Button';
import { BigNumber } from '@/components/ui/BigNumber';
import { Tooltip } from '@/components/ui/Tooltip';
import { EmptyState } from '@/components/ui/EmptyState';
import { Checkbox, Field, Textarea } from '@/components/ui/Field';
import { SegmentedControl } from '@/components/ui/Tabs';
import { QueryErrorState, StaleDataBanner, formatAsOf } from '@/components/ui/QueryState';
import { EChart } from '@/components/charts/LazyEChart';
import { useChartTheme } from '@/lib/chart-theme';
import { get, patch, post, put, apiErrorMessage } from '@/lib/api';
import { cn, formatUptime, timeAgo } from '@/lib/utils';
import type { HealthState } from '@/lib/status';
import { MAX_WATCHED_SERVICES, parseServiceList, serviceBadge } from '@/lib/agentServices';
import { useToastStore } from '@/stores/toast';
import { useIsEditor } from '@/stores/auth';
import { useConfirm } from '@/hooks/useConfirm';
import type { Agent, AgentServiceState, AgentSnapshot } from '@/types';
import { UsageBar, usageState } from '../_components/agentStatus';

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

  const query = useQuery({
    queryKey: ['agent', agentId],
    queryFn: () => get<AgentDetail>(`/api/v1/agents/${agentId}`),
    enabled: agentId > 0,
    refetchInterval: 15_000,
  });
  const { data, isLoading } = query;

  const toast = useToastStore((s) => s.show);
  const router = useRouter();
  const chart = useChartTheme();
  const { confirm, ConfirmDialogElement } = useConfirm();
  const [uninstalling, setUninstalling] = useState(false);

  useEffect(() => {
    document.title = `${data?.name ?? 'Agent'} | Nodeglow`;
  }, [data?.name]);

  const online = data?.last_seen
    ? Date.now() - new Date(data.last_seen).getTime() < 120_000
    : false;
  const state: { status: HealthState | 'disabled'; label: string } = !data
    ? { status: 'unknown', label: 'No data' }
    : data.enabled === false
      ? { status: 'disabled', label: 'Disabled' }
      : online
        ? { status: 'ok', label: 'Online' }
        : data.last_seen
          ? { status: 'down', label: 'Offline' }
          : { status: 'unknown', label: 'Never connected' };

  const latest = data?.snapshots?.[0];
  const snapshots = data?.snapshots ?? [];
  const asOf = formatAsOf(query.dataUpdatedAt);

  async function handleUninstall() {
    const ok = await confirm({
      title: 'Uninstall agent',
      description: `Uninstall the agent on "${data?.name}"? This stops the agent process, removes the scheduled task/service, deletes all files on the remote machine, and removes the agent from Nodeglow.`,
      confirmLabel: 'Uninstall',
      variant: 'danger',
    });
    if (!ok) return;
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
      <Breadcrumbs items={[{ label: 'Agents & probes', href: '/agents' }, { label: data?.name ?? `Agent #${agentId}` }]} />
      <PageHeader
        title={data?.name ?? (isLoading ? 'Loading agent…' : `Agent #${agentId}`)}
        status={data ? <StatusPill status={state.status}>{state.label}</StatusPill> : undefined}
        description={
          data ? (
            <>
              {data.hostname && <span className="font-mono">{data.hostname}</span>}
              {data.hostname && ' · '}
              {data.last_seen ? `Last report ${timeAgo(data.last_seen)}` : 'No report received yet'}
              {asOf && ` · updated ${asOf}`}
            </>
          ) : undefined
        }
        actions={
          <>
            <Link href="/agents" className={buttonClasses({ variant: 'ghost', size: 'sm' })}>
              <ArrowLeft size={14} aria-hidden="true" /> Back
            </Link>
            <Tooltip content={online ? 'Sends an uninstall command on the next check-in' : 'The agent must be online to receive the uninstall command'}>
              <Button variant="danger" size="sm" onClick={handleUninstall} loading={uninstalling} disabled={!online}>
                <Trash2 size={14} aria-hidden="true" />
                {uninstalling ? 'Sending…' : 'Uninstall'}
              </Button>
            </Tooltip>
          </>
        }
      />

      {query.isError && !data ? (
        <Card><QueryErrorState error={query.error} onRetry={query.refetch} title="Could not load the agent" /></Card>
      ) : (
        <>
          {query.isError && data && <StaleDataBanner error={query.error} onRetry={query.refetch} updatedAt={query.dataUpdatedAt} />}

          <Card className="mb-4" padding="sm">
            {isLoading ? (
              <Skeleton className="h-10 w-full" />
            ) : data ? (
              <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3 lg:grid-cols-5">
                <Fact label="Platform">{data.platform ?? '—'}</Fact>
                <Fact label="Architecture">{data.arch ?? '—'}</Fact>
                <Fact label="Agent version">{data.agent_version ? `v${data.agent_version}` : '—'}</Fact>
                <Fact label="Last seen">{data.last_seen ? new Date(data.last_seen).toLocaleString() : 'Never'}</Fact>
                <Fact label="Uptime">{latest?.uptime_s != null ? formatUptime(latest.uptime_s) : '—'}</Fact>
              </dl>
            ) : null}
          </Card>

          <div className="mb-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
            <MetricCard icon={Cpu} label="CPU" value={latest?.cpu_pct} loading={isLoading} stale={!!data && !online} />
            <MetricCard
              icon={MemoryStick}
              label="Memory"
              value={latest?.mem_pct}
              extra={latest && latest.mem_used_mb != null && latest.mem_total_mb != null
                ? `${(latest.mem_used_mb / 1024).toFixed(1)} / ${(latest.mem_total_mb / 1024).toFixed(1)} GB`
                : undefined}
              loading={isLoading}
              stale={!!data && !online}
            />
            <MetricCard icon={HardDrive} label="Disk" value={latest?.disk_pct} loading={isLoading} stale={!!data && !online} />
          </div>

          {data && <WatchedServices agentId={agentId} data={data} online={online} />}
          {data && <LogSettings agentId={agentId} data={data} />}

          <Card className="mb-4" as="section" aria-labelledby="agent-perf-title">
            <CardHeader
              title="Performance history"
              titleId="agent-perf-title"
              meta={snapshots.length ? `${snapshots.length} reports` : undefined}
            />
            {isLoading ? (
              <Skeleton className="h-[250px] w-full" />
            ) : snapshots.length === 0 ? (
              <EmptyState compact icon={Monitor} title="No reports yet" description="Metrics appear after the first check-in of the agent." />
            ) : (
              <EChart
                height={250}
                ariaLabel={`CPU, memory and disk usage over the last ${snapshots.length} reports`}
                option={{
                  tooltip: { trigger: 'axis' },
                  legend: { data: ['CPU', 'Memory', 'Disk'] },
                  xAxis: {
                    type: 'category',
                    data: snapshots.map((s) =>
                      new Date(s.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
                    ).reverse(),
                  },
                  yAxis: { type: 'value', max: 100, axisLabel: { formatter: '{value}%' } },
                  series: [
                    { name: 'CPU', type: 'line', data: snapshots.map((s) => s.cpu_pct).reverse(), color: chart.series[0], smooth: true, showSymbol: false, areaStyle: { color: chart.accentFill } },
                    { name: 'Memory', type: 'line', data: snapshots.map((s) => s.mem_pct).reverse(), color: chart.series[1], smooth: true, showSymbol: false },
                    { name: 'Disk', type: 'line', data: snapshots.map((s) => s.disk_pct).reverse(), color: chart.series[2], smooth: true, showSymbol: false },
                  ],
                }}
              />
            )}
          </Card>
        </>
      )}
      {ConfirmDialogElement}
    </div>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-meta text-fg-3">{label}</dt>
      <dd className="truncate text-ui text-fg">{children}</dd>
    </div>
  );
}

const SERVICE_STATE: Record<string, HealthState> = {
  ok: 'ok',
  critical: 'down',
  warning: 'warning',
  info: 'unknown',
  none: 'unknown',
};

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
  const error = parsed.invalid.length > 0
    ? `Invalid name(s): ${parsed.invalid.join(', ')}`
    : tooMany
      ? `At most ${MAX_WATCHED_SERVICES} services can be watched.`
      : undefined;

  return (
    <Card className="mb-4" as="section" aria-labelledby="agent-services-title">
      <CardHeader
        title="Watched services"
        titleId="agent-services-title"
        meta={data.services_reported_at ? `reported ${timeAgo(data.services_reported_at)}` : undefined}
        actions={canEdit && !editing ? (
          <Button size="sm" variant="ghost" onClick={startEdit}>
            <Pencil size={14} aria-hidden="true" /> Edit
          </Button>
        ) : undefined}
      />

      {editing ? (
        <div className="space-y-3">
          <Field
            label="Service names"
            hint={isWindows
              ? 'One Windows service name per line (the service name, not the display name — e.g. Spooler, W32Time).'
              : 'One systemd unit per line (e.g. nginx, sshd, docker.service).'}
            error={error}
          >
            <Textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={5}
              placeholder={isWindows ? 'Spooler\nW32Time' : 'nginx\nsshd'}
              className="font-mono text-meta"
            />
          </Field>
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)} disabled={saving}>
              <X size={14} aria-hidden="true" /> Cancel
            </Button>
            <Button size="sm" onClick={handleSave} loading={saving} disabled={parsed.invalid.length > 0 || tooMany}>
              {!saving && <Save size={14} aria-hidden="true" />}
              {saving ? 'Saving…' : 'Save'}
            </Button>
          </div>
        </div>
      ) : watched.length === 0 ? (
        <p className="text-ui text-fg-2">
          No services watched.{canEdit ? ' Add services to get an incident when one stops running.' : ''}
        </p>
      ) : (
        <>
          {neverReported && (
            <p className="mb-3 rounded-ctl border border-degraded/40 bg-degraded-soft px-3 py-2 text-meta text-degraded" role="status">
              The agent has not reported service states yet. It picks up the list on its next check-in;
              agents older than this feature need an update first.
            </p>
          )}
          {!neverReported && !online && (
            <p className="mb-3 text-meta text-fg-2">Agent is offline — states below are from its last report and may be outdated.</p>
          )}
          <ul className="divide-y divide-border">
            {services.map((s) => {
              const badge = serviceBadge(s.state);
              // Offline agent: the last known state is not a fresh observation.
              const st: HealthState = !online ? 'unknown' : SERVICE_STATE[badge.severity] ?? 'unknown';
              return (
                <li key={s.name} className="flex items-center justify-between gap-3 py-2">
                  <div className="min-w-0">
                    <p className="truncate font-mono text-ui text-fg">{s.name}</p>
                    <p className="text-micro text-fg-3">
                      {s.start_type ? `Start: ${s.start_type}` : ''}
                      {s.start_type && s.since ? ' · ' : ''}
                      {s.since ? `since ${new Date(s.since).toLocaleString()}` : ''}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {s.alerted && <Badge variant="severity" severity="critical">Incident</Badge>}
                    <StatusPill status={st}>{badge.label}{!online && s.state ? ' (last report)' : ''}</StatusPill>
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </Card>
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
    <Card className="mb-4" as="section" aria-labelledby="agent-logs-title">
      <CardHeader title="Log collection" titleId="agent-logs-title" />

      <div className="space-y-5">
        <div>
          <p className="ng-label">Agent log level</p>
          <p className="mb-2 text-meta text-fg-3">Controls which of the agent&apos;s own logs are uploaded</p>
          <SegmentedControl
            label="Agent log level"
            value={agentLogLevel as 'off' | 'errors' | 'all'}
            onChange={setAgentLogLevel}
            options={[
              { value: 'off', label: 'Off' },
              { value: 'errors', label: 'Errors only' },
              { value: 'all', label: 'All' },
            ]}
          />
        </div>

        {isWindows && (
          <fieldset>
            <legend className="ng-label">Event log severity levels</legend>
            <p className="mb-2 text-meta text-fg-3">Which Windows Event Log severity levels to collect</p>
            <div className="flex flex-wrap gap-x-5 gap-y-2">
              {SEVERITY_LEVELS.map((s) => (
                <Checkbox key={s.value} label={s.label} checked={logLevels.has(s.value)} onChange={() => toggleLevel(s.value)} />
              ))}
            </div>
          </fieldset>
        )}

        {isWindows && (
          <fieldset>
            <legend className="ng-label">Event log channels</legend>
            <p className="mb-2 text-meta text-fg-3">Which Windows Event Log channels to monitor</p>
            <div className="grid grid-cols-1 gap-x-5 gap-y-2 sm:grid-cols-2 lg:grid-cols-3">
              {WINDOWS_CHANNELS.map((ch) => (
                <Checkbox key={ch.key} label={ch.label} checked={logChannels.has(ch.key)} onChange={() => toggleChannel(ch.key)} />
              ))}
            </div>
          </fieldset>
        )}

        <Field
          label="Custom log file paths"
          hint={isWindows
            ? 'One file path per line (e.g. C:\\Logs\\app.log)'
            : 'One file path per line, glob patterns supported (e.g. /var/log/auth.log)'}
        >
          <Textarea
            value={logFilePaths}
            onChange={(e) => setLogFilePaths(e.target.value)}
            rows={3}
            placeholder={isWindows ? 'C:\\Logs\\app.log' : '/var/log/auth.log\n/var/log/syslog'}
            className="font-mono text-meta"
          />
        </Field>

        <div className="flex justify-end">
          <Button size="sm" onClick={handleSave} loading={saving}>
            {!saving && <Save size={14} aria-hidden="true" />}
            {saving ? 'Saving…' : 'Save settings'}
          </Button>
        </div>
      </div>
    </Card>
  );
}

function MetricCard({
  icon: Icon,
  label,
  value,
  extra,
  loading,
  stale,
}: {
  icon: LucideIcon;
  label: string;
  value?: number | null;
  extra?: string;
  loading: boolean;
  stale?: boolean;
}) {
  const has = value != null;
  return (
    <Card>
      <div className="mb-2 flex items-center gap-2">
        <Icon size={15} className="text-fg-3" aria-hidden="true" />
        <span className="text-meta text-fg-2">{label}</span>
        {stale && has && <span className="ml-auto text-micro text-fg-3">last report</span>}
      </div>
      {loading ? (
        <Skeleton className="h-9 w-24" />
      ) : (
        <>
          <BigNumber
            value={has ? Math.round(value) : null}
            unit={has ? '%' : undefined}
            state={has && !stale && usageState(value) !== 'ok' ? usageState(value) : undefined}
            stale={stale}
            label={extra}
          />
          <UsageBar label={`${label} usage`} value={value} stale={stale} className={cn('mt-3 [&>div:first-child]:sr-only')} />
        </>
      )}
    </Card>
  );
}
