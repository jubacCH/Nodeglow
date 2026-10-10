'use client';

import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Download, RefreshCw } from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { BigNumber } from '@/components/ui/BigNumber';
import { Button } from '@/components/ui/Button';
import { Card, CardHeader } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryState, formatAsOf } from '@/components/ui/QueryState';
import { Skeleton } from '@/components/ui/Skeleton';
import { StatusDot } from '@/components/ui/StatusDot';
import { StatusPill } from '@/components/ui/StatusPill';
import { Tag } from '@/components/ui/Tag';
import { UpdateProgress } from '@/components/system/UpdateProgress';
import { useConfirm } from '@/hooks/useConfirm';
import { get, post } from '@/lib/api';
import { HEALTH_LABEL, STATE_TEXT, type HealthState } from '@/lib/status';
import type { UpdateRunState } from '@/lib/updateSteps';
import { cn } from '@/lib/utils';
import { useToastStore } from '@/stores/toast';

/* ---------- Types ---------- */

interface SystemStatus {
  application: {
    version: string;
    uptime_seconds: number;
    uptime_human: string;
    python_version: string;
    platform: string;
    hostname: string;
    pid: number;
    start_time: string;
    git_commit: string;
  };
  system: {
    cpu_count: number;
    cpu_pct: number;
    load_1m: number;
    load_5m: number;
    load_15m: number;
    mem_total_gb: number;
    mem_used_gb: number;
    mem_pct: number;
    swap_total_gb: number;
    swap_used_gb: number;
    swap_pct: number;
    disk_total_gb: number;
    disk_used_gb: number;
    disk_pct: number;
    disk_io: { read_gb?: number; write_gb?: number };
    net_io: { sent_gb?: number; recv_gb?: number };
    net_if: Array<{ name: string; ip: string; up: boolean; speed: number }>;
  };
  process: {
    rss_mb: number;
    vms_mb: number;
    threads: number;
    open_files: number;
    connections: number;
    cpu_user: number;
    cpu_system: number;
  };
  database: {
    db_size?: string;
    host_count?: number;
    result_count?: number;
    config_count?: number;
    snapshot_count?: number;
    syslog_count?: number;
    oldest_ping?: string;
    newest_ping?: string;
    error?: string;
  };
  disk_forecast: {
    growth_gb_per_day: number;
    days_until_full: number | null;
    trend: 'stable' | 'normal' | 'warning' | 'critical';
  } | null;
  top_tables: Array<{ name: string; size: string; rows: number }>;
  pool: {
    size?: number;
    checked_in?: number;
    checked_out?: number;
    overflow?: number;
    max_overflow?: number;
  };
  scheduler_jobs: Array<{
    id: string;
    name: string;
    trigger: string;
    next_run: string;
  }>;
  integrations: Array<{
    type: string;
    name: string;
    ok: boolean | null;
    last_check: string;
    error: string | null;
  }>;
  dashboard_perf: {
    total_ms: number;
    sections: Array<{ name: string; ms: number }>;
    timestamp: string;
  } | null;
  operational: {
    ping_stats: {
      checks_1h?: number;
      success_rate?: number;
      avg_latency?: number;
      max_latency?: number;
    };
    syslog_status: {
      running?: boolean;
      buffer_size?: number;
      msg_per_min?: number;
    };
    ssl_expiring: Array<{ name: string; hostname: string; days: number }>;
    notification_channels: { telegram?: boolean; discord?: boolean; email?: boolean; teams?: boolean; slack?: boolean; ntfy?: boolean };
    incidents: { open?: number; acknowledged?: number; resolved?: number; total?: number };
    alert_rules: { total?: number; enabled?: number; syslog_rules?: number; last_triggered?: string };
    maintenance: { active?: number; timed?: number; indefinite?: number };
    log_intelligence: { templates?: number; baselines?: number; precursors?: number };
    data_retention: { ping_age?: string; snap_age?: string; syslog_age?: string };
  };
}

interface UpdateInfo {
  update_available: boolean;
  local: { commit?: string; version?: string };
  remote_commit?: string;
  remote_version?: string;
  commits_behind?: number;
  changelog?: { hash: string; message: string }[];
  error?: string;
}

/* ---------- Health derivation (honest: missing data is unknown, never OK) ---------- */

function pctState(pct: number | null | undefined): HealthState | undefined {
  if (pct == null) return undefined;
  if (pct >= 90) return 'down';
  if (pct >= 75) return 'warning';
  return undefined; // normal utilisation is not a health claim
}

function pingState(p: SystemStatus['operational']['ping_stats']): HealthState {
  if (!p.checks_1h || p.success_rate == null) return 'unknown';
  if (p.success_rate >= 95) return 'ok';
  if (p.success_rate >= 80) return 'degraded';
  return 'down';
}

function syslogState(s: SystemStatus['operational']['syslog_status']): HealthState {
  if (s.running === undefined) return 'unknown';
  return s.running ? 'ok' : 'down';
}

function integrationState(ok: boolean | null): HealthState {
  if (ok === true) return 'ok';
  if (ok === false) return 'down';
  return 'unknown';
}

/** `n.toLocaleString()`, or an em dash when the backend did not report it. */
function n(v: number | undefined | null): string {
  return v == null ? '—' : v.toLocaleString();
}

/* ---------- Small pieces ---------- */

function Meter({ value, label }: { value: number; label: string }) {
  const clamped = Math.min(100, Math.max(0, value));
  const state = pctState(value);
  return (
    <div
      role="meter"
      aria-label={label}
      aria-valuenow={clamped}
      aria-valuemin={0}
      aria-valuemax={100}
      className="h-[6px] w-full overflow-hidden rounded-pill bg-surface-3"
    >
      <div
        className={cn('h-full rounded-pill transition-[width] duration-500', state === 'down' ? 'bg-down' : state === 'warning' ? 'bg-warning' : 'bg-accent')}
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}

function StatRow({ label, value, mono }: { label: string; value: ReactNode; mono?: boolean }) {
  return (
    <div className="flex min-w-0 items-baseline justify-between gap-3 py-1">
      <dt className="shrink-0 text-ui text-fg-2">{label}</dt>
      <dd className={cn('min-w-0 truncate text-right text-ui text-fg', mono ? 'font-mono text-meta' : 'num')}>{value}</dd>
    </div>
  );
}

function ComponentRow({ name, state, detail, label }: { name: string; state: HealthState; detail: ReactNode; label?: string }) {
  return (
    <li className="flex min-w-0 items-center gap-3 py-2">
      <StatusDot status={state} label="" />
      <span className="min-w-0 flex-1 truncate text-ui text-fg">{name}</span>
      <span className="hidden min-w-0 truncate text-meta text-fg-2 sm:inline">{detail}</span>
      <StatusPill status={state} size="sm">{label ?? HEALTH_LABEL[state]}</StatusPill>
    </li>
  );
}

function UsageCard({ title, pct, primary, secondary, children }: { title: string; pct: number; primary: ReactNode; secondary?: ReactNode; children?: ReactNode }) {
  const state = pctState(pct);
  return (
    <Card>
      <CardHeader title={title} meta={primary} />
      <div className="mb-2 flex items-end justify-between gap-2">
        <BigNumber size="sm" value={pct} unit="%" state={state} />
        {state && <StatusPill status={state} size="sm">{state === 'down' ? 'Critical' : 'High'}</StatusPill>}
      </div>
      <Meter value={pct} label={`${title} usage`} />
      {secondary && <p className="mt-2 text-meta text-fg-2">{secondary}</p>}
      {children}
    </Card>
  );
}

function PageSkeleton() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Loading">
      <Skeleton className="h-40 w-full rounded-card" />
      <div className="grid gap-4 md:grid-cols-3">
        <Skeleton className="h-32 rounded-card" /><Skeleton className="h-32 rounded-card" /><Skeleton className="h-32 rounded-card" />
      </div>
      <Skeleton className="h-56 w-full rounded-card" />
    </div>
  );
}

/* ---------- Page ---------- */

export default function SystemStatusPage() {
  useEffect(() => { document.title = 'System Status | Nodeglow'; }, []);
  const toast = useToastStore((s) => s.show);
  const { confirm, ConfirmDialogElement } = useConfirm();
  const [checking, setChecking] = useState(false);
  const [updating, setUpdating] = useState(false);
  const [runActive, setRunActive] = useState(false);
  const [updateInfo, setUpdateInfo] = useState<UpdateInfo | null>(null);

  // A run started in another tab (or before a reload) keeps rendering here.
  useEffect(() => {
    let cancelled = false;
    get<UpdateRunState>('/api/update/status')
      .then((s) => { if (!cancelled && s?.status === 'running') setRunActive(true); })
      .catch(() => { /* sidecar unavailable — nothing to resume */ });
    return () => { cancelled = true; };
  }, []);

  const query = useQuery({
    queryKey: ['system-status'],
    queryFn: () => get<SystemStatus>('/api/system/status'),
    refetchInterval: 15_000,
  });

  async function checkUpdate() {
    setChecking(true);
    try {
      const info = await get<UpdateInfo>('/api/update/check');
      setUpdateInfo(info);
    } catch {
      toast('Failed to check for updates', 'error');
    } finally {
      setChecking(false);
    }
  }

  async function applyUpdate() {
    const ok = await confirm({
      title: 'Update Nodeglow now?',
      description: 'Nodeglow backs up the database, applies migrations and restarts. The interface is unavailable for a short time.',
      confirmLabel: 'Update now',
    });
    if (!ok) return;
    setUpdating(true);
    try {
      const result = await post<{ ok: boolean; run_id?: string; message?: string; error?: string }>('/api/update/apply');
      if (result.ok) {
        setRunActive(true);
        toast(result.message || 'Update started', 'success');
      } else {
        toast(result.error || 'Update failed', 'error');
      }
    } catch (err) {
      let msg = 'Failed to apply update';
      if (err instanceof Error && 'data' in err) {
        try {
          const parsed = JSON.parse((err as { data?: string }).data ?? '');
          if (parsed?.error) msg = parsed.error;
        } catch { /* use default */ }
      }
      toast(msg, 'error');
    } finally {
      setUpdating(false);
    }
  }

  const handleRunFinished = useCallback((s: UpdateRunState) => {
    setRunActive(false);
    if (s.status === 'done') toast('Update complete', 'success');
    else if (s.status === 'failed') toast(s.error || 'Update failed', 'error');
  }, [toast]);

  const asOf = formatAsOf(query.dataUpdatedAt);

  return (
    <div>
      <PageHeader
        title="System status"
        description={asOf ? `Nodeglow backend health and diagnostics · updated ${asOf} · every 15 s` : 'Nodeglow backend health and diagnostics'}
        actions={
          <Button variant="secondary" size="sm" onClick={() => query.refetch()} loading={query.isFetching && !query.isLoading}>
            {!(query.isFetching && !query.isLoading) && <RefreshCw size={14} aria-hidden="true" />}
            Refresh
          </Button>
        }
      />

      <QueryState query={query} loading={<PageSkeleton />} errorTitle="Could not load system status">
        {(status) => {
          const app = status.application;
          const sys = status.system;
          const db = status.database;
          const ops = status.operational;
          const ints = status.integrations ?? [];
          const intDown = ints.filter((i) => i.ok === false).length;
          const intUnknown = ints.filter((i) => i.ok == null).length;
          const intState: HealthState = ints.length === 0 ? 'unknown' : intDown > 0 ? 'down' : intUnknown > 0 ? 'unknown' : 'ok';
          const jobs = status.scheduler_jobs ?? [];
          const channels = Object.entries(ops.notification_channels).filter(([, on]) => on).map(([k]) => k);
          const forecast = status.disk_forecast;

          return (
            <div className="space-y-4">
              {/* Components: each says what it is based on; no data is "No data", never OK. */}
              <div className="grid gap-4 lg:grid-cols-12">
                <Card as="section" aria-labelledby="h-components" className="lg:col-span-7">
                  <CardHeader title="Components" titleId="h-components" meta="Measured by the backend itself" />
                  <ul className="divide-y divide-border">
                    <ComponentRow
                      name="API"
                      state={query.isError ? 'down' : 'ok'}
                      detail={query.isError ? `Last response ${asOf}` : `Responded ${asOf}`}
                      label={query.isError ? 'Not responding' : 'Reachable'}
                    />
                    <ComponentRow
                      name="Database"
                      state={db.error ? 'down' : db.db_size ? 'ok' : 'unknown'}
                      detail={db.error ?? (db.db_size ? `${db.db_size} · ${status.pool.checked_out ?? '—'} of ${status.pool.size ?? '—'} connections in use` : 'No figures reported')}
                    />
                    <ComponentRow
                      name="Ping checks"
                      state={pingState(ops.ping_stats)}
                      detail={ops.ping_stats.checks_1h ? `${ops.ping_stats.success_rate}% success · ${n(ops.ping_stats.checks_1h)} checks in 1 h` : 'No checks in the last hour'}
                    />
                    <ComponentRow
                      name="Syslog receiver"
                      state={syslogState(ops.syslog_status)}
                      detail={ops.syslog_status.msg_per_min !== undefined ? `${ops.syslog_status.msg_per_min} msg/min` : ops.syslog_status.running ? 'Running' : ops.syslog_status.running === false ? 'Stopped' : 'Not reported'}
                      label={ops.syslog_status.running === false ? 'Stopped' : undefined}
                    />
                    <ComponentRow
                      name="Integrations"
                      state={intState}
                      detail={ints.length === 0 ? 'None configured' : `${ints.length - intDown - intUnknown} ok · ${intDown} failing · ${intUnknown} no data`}
                      label={ints.length === 0 ? 'None' : undefined}
                    />
                    <ComponentRow
                      name="Background jobs"
                      state={jobs.length > 0 ? 'ok' : 'unknown'}
                      detail={jobs.length > 0 ? `${jobs.length} scheduled · ${jobs.filter((j) => j.next_run === 'paused').length} paused` : 'Scheduler reported no jobs'}
                      label={jobs.length > 0 ? 'Scheduled' : undefined}
                    />
                  </ul>
                </Card>

                <Card as="section" aria-labelledby="h-app" className="lg:col-span-5">
                  <CardHeader title="Application" titleId="h-app" actions={<Badge className="font-mono">{app.version || 'dev'}</Badge>} />
                  <dl>
                    <StatRow label="Uptime" value={app.uptime_human} />
                    <StatRow label="Started" value={app.start_time} />
                    <StatRow label="Hostname" value={app.hostname} mono />
                    <StatRow label="Git commit" value={app.git_commit} mono />
                    <StatRow label="Python" value={app.python_version} />
                    <StatRow label="PID" value={app.pid} mono />
                    <StatRow label="Platform" value={app.platform.split('-').slice(0, 2).join(' ')} />
                  </dl>
                </Card>
              </div>

              {/* Resources */}
              <div className="grid gap-4 md:grid-cols-3">
                <UsageCard
                  title="CPU"
                  pct={sys.cpu_pct}
                  primary={`${sys.cpu_count} cores`}
                  secondary={<>Load <span className="num">{sys.load_1m} / {sys.load_5m} / {sys.load_15m}</span></>}
                />
                <UsageCard
                  title="Memory"
                  pct={sys.mem_pct}
                  primary={<span className="num">{sys.mem_used_gb} / {sys.mem_total_gb} GB</span>}
                  secondary={sys.swap_total_gb > 0 ? <>Swap <span className="num">{sys.swap_used_gb} / {sys.swap_total_gb} GB ({sys.swap_pct}%)</span></> : undefined}
                />
                <UsageCard
                  title="Disk"
                  pct={sys.disk_pct}
                  primary={<span className="num">{sys.disk_used_gb} / {sys.disk_total_gb} GB</span>}
                  secondary={sys.disk_io.read_gb !== undefined ? <>I/O <span className="num">{sys.disk_io.read_gb} GB read / {sys.disk_io.write_gb} GB write</span></> : undefined}
                >
                  {forecast && (
                    <p className={cn(
                      'mt-2 border-t border-border pt-2 text-meta',
                      forecast.trend === 'critical' ? 'text-down' : forecast.trend === 'warning' ? 'text-warning' : 'text-fg-2',
                    )}>
                      {forecast.trend === 'stable' ? (
                        'Stable — no significant growth'
                      ) : (
                        <>
                          <span className="num">+{forecast.growth_gb_per_day} GB/day</span>
                          {forecast.days_until_full != null && (
                            <> — full in ~{forecast.days_until_full < 1
                              ? '<1 day'
                              : forecast.days_until_full < 30
                                ? `${Math.round(forecast.days_until_full)}d`
                                : `${Math.round(forecast.days_until_full / 30)}mo`}</>
                          )}
                        </>
                      )}
                    </p>
                  )}
                </UsageCard>
              </div>

              {/* Software updates */}
              <Card as="section" aria-labelledby="h-updates">
                <CardHeader
                  title="Software updates"
                  titleId="h-updates"
                  actions={
                    <>
                      <Button size="sm" variant="secondary" onClick={checkUpdate} loading={checking}>
                        {!checking && <RefreshCw size={14} aria-hidden="true" />}
                        {checking ? 'Checking…' : 'Check now'}
                      </Button>
                      {updateInfo?.update_available && (
                        <Button size="sm" onClick={applyUpdate} loading={updating || runActive}>
                          {!(updating || runActive) && <Download size={14} aria-hidden="true" />}
                          {updating || runActive ? 'Updating…' : 'Update now'}
                        </Button>
                      )}
                    </>
                  }
                />
                {updateInfo ? (
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-ui text-fg-2">Installed</span>
                      <Badge className="font-mono">{updateInfo.local?.version || updateInfo.local?.commit || '—'}</Badge>
                      {updateInfo.update_available ? (
                        <Badge tone="accent">
                          {updateInfo.commits_behind} commit{updateInfo.commits_behind !== 1 ? 's' : ''} behind
                        </Badge>
                      ) : !updateInfo.error ? (
                        <StatusPill status="ok" size="sm">Up to date</StatusPill>
                      ) : null}
                      {updateInfo.error && <StatusPill status="warning" size="sm">Check failed</StatusPill>}
                    </div>
                    {updateInfo.error && <p className="mt-2 text-meta text-warning">{updateInfo.error}</p>}
                    {updateInfo.changelog && updateInfo.changelog.length > 0 && (
                      <ul className="mt-3 max-h-[200px] space-y-1 overflow-y-auto rounded-ctl border border-border bg-surface-2 p-3">
                        {updateInfo.changelog.map((entry) => (
                          <li key={entry.hash} className="flex gap-2 text-meta">
                            <code className="shrink-0 font-mono text-accent">{entry.hash}</code>
                            <span className="text-fg-2">{entry.message}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                ) : (
                  <p className="text-ui text-fg-2">Not checked yet in this session. “Check now” compares with the release channel.</p>
                )}
                <UpdateProgress active={runActive} onFinished={handleRunFinished} />
              </Card>

              {/* Operations + Database */}
              <div className="grid gap-4 md:grid-cols-2">
                <Card as="section" aria-labelledby="h-ops">
                  <CardHeader title="Operations" titleId="h-ops" />
                  <dl>
                    <StatRow label="Ping latency (avg / max)" value={ops.ping_stats.avg_latency != null ? `${ops.ping_stats.avg_latency} / ${ops.ping_stats.max_latency} ms` : '—'} />
                    <StatRow
                      label="Incidents (open / ack / resolved)"
                      value={ops.incidents.total !== undefined ? (
                        <span className={(ops.incidents.open ?? 0) > 0 ? 'text-down' : undefined}>
                          {n(ops.incidents.open)} / {n(ops.incidents.acknowledged)} / {n(ops.incidents.resolved)}
                        </span>
                      ) : '—'}
                    />
                    <StatRow label="Alert rules (enabled / total)" value={ops.alert_rules.total !== undefined ? `${n(ops.alert_rules.enabled)} / ${n(ops.alert_rules.total)}` : '—'} />
                    <StatRow label="Hosts in maintenance" value={<span className={(ops.maintenance.active ?? 0) > 0 ? STATE_TEXT.maint : undefined}>{n(ops.maintenance.active)}</span>} />
                    <div className="flex min-w-0 items-baseline justify-between gap-3 py-1">
                      <dt className="shrink-0 text-ui text-fg-2">Notification channels</dt>
                      <dd className="flex min-w-0 flex-wrap justify-end gap-1">
                        {channels.length > 0
                          ? channels.map((c) => <Badge key={c} className="capitalize">{c}</Badge>)
                          : <span className="text-meta text-fg-3">None configured</span>}
                      </dd>
                    </div>
                  </dl>
                </Card>

                <Card as="section" aria-labelledby="h-db">
                  <CardHeader title="Database" titleId="h-db" actions={db.db_size ? <Badge className="num">{db.db_size}</Badge> : undefined} />
                  {db.error ? (
                    <p role="alert" className="text-ui text-down">{db.error}</p>
                  ) : (
                    <>
                      <dl className="grid gap-x-6 sm:grid-cols-2">
                        <StatRow label="Hosts" value={n(db.host_count)} />
                        <StatRow label="Ping results" value={n(db.result_count)} />
                        <StatRow label="Integrations" value={n(db.config_count)} />
                        <StatRow label="Snapshots" value={n(db.snapshot_count)} />
                        <StatRow label="Syslog messages" value={n(db.syslog_count)} />
                      </dl>
                      {status.top_tables?.length > 0 && (
                        <div className="mt-3 border-t border-border pt-3">
                          <p className="mb-1 text-meta text-fg-3">Largest tables</p>
                          <ul className="space-y-0.5">
                            {status.top_tables.slice(0, 5).map((t) => (
                              <li key={t.name} className="flex min-w-0 justify-between gap-3 text-meta">
                                <span className="truncate font-mono text-fg-2">{t.name}</span>
                                <span className="num shrink-0 text-fg-3">{t.size} · {t.rows.toLocaleString()} rows</span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </>
                  )}
                </Card>
              </div>

              {/* Integrations + jobs */}
              <div className="grid gap-4 md:grid-cols-2">
                <Card as="section" aria-labelledby="h-int">
                  <CardHeader title="Integration health" titleId="h-int" meta={ints.length ? `${ints.length} configured` : undefined} />
                  {ints.length > 0 ? (
                    <ul className="max-h-[280px] divide-y divide-border overflow-y-auto">
                      {ints.map((int) => {
                        const st = integrationState(int.ok);
                        return (
                          <li key={`${int.type}-${int.name}`} className="flex min-w-0 items-center gap-2 py-1.5">
                            <StatusDot status={st} />
                            <span className="min-w-0 flex-1 truncate text-ui text-fg">{int.name}</span>
                            {int.error && <span className="max-w-[160px] truncate text-meta text-down" title={int.error}>{int.error}</span>}
                            <Badge>{int.type}</Badge>
                            <span className="num shrink-0 text-meta text-fg-3">{int.last_check}</span>
                          </li>
                        );
                      })}
                    </ul>
                  ) : (
                    <EmptyState compact title="No integrations configured" description="Integration checks appear here once one is added." />
                  )}
                </Card>

                <Card as="section" aria-labelledby="h-jobs">
                  <CardHeader title="Background jobs" titleId="h-jobs" meta={jobs.length ? `${jobs.length} jobs` : undefined} />
                  {jobs.length > 0 ? (
                    <ul className="max-h-[280px] divide-y divide-border overflow-y-auto">
                      {jobs.map((job) => (
                        <li key={job.id} className="flex min-w-0 items-center gap-2 py-1.5">
                          <span className="min-w-0 flex-1 truncate text-ui text-fg">{job.name}</span>
                          {job.next_run === 'paused'
                            ? <Tag>Paused</Tag>
                            : <span className="shrink-0 font-mono text-meta text-fg-2" title="Next run">{job.next_run}</span>}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <EmptyState compact title="No scheduler jobs reported" />
                  )}
                </Card>
              </div>

              {/* Process + pool */}
              <div className="grid gap-4 md:grid-cols-2">
                <Card as="section" aria-labelledby="h-proc">
                  <CardHeader title="Process" titleId="h-proc" />
                  <dl className="grid gap-x-6 sm:grid-cols-2">
                    <StatRow label="RSS" value={`${status.process.rss_mb} MB`} />
                    <StatRow label="VMS" value={`${status.process.vms_mb} MB`} />
                    <StatRow label="Threads" value={status.process.threads} />
                    <StatRow label="Open files" value={status.process.open_files} />
                    <StatRow label="Connections" value={status.process.connections} />
                    <StatRow label="CPU user / sys" value={`${status.process.cpu_user}s / ${status.process.cpu_system}s`} />
                  </dl>
                </Card>
                <Card as="section" aria-labelledby="h-pool">
                  <CardHeader title="Connection pool" titleId="h-pool" />
                  <dl className="grid gap-x-6 sm:grid-cols-2">
                    <StatRow label="Pool size" value={n(status.pool.size)} />
                    <StatRow label="Checked in" value={n(status.pool.checked_in)} />
                    <StatRow label="Checked out" value={n(status.pool.checked_out)} />
                    <StatRow label="Overflow" value={`${n(status.pool.overflow)} / ${n(status.pool.max_overflow)}`} />
                  </dl>
                </Card>
              </div>

              {/* Dashboard API performance */}
              {status.dashboard_perf?.sections && (() => {
                const perf = status.dashboard_perf;
                const slow = perf.total_ms >= 3000 ? 'down' : perf.total_ms >= 1000 ? 'degraded' : null;
                return (
                  <Card as="section" aria-labelledby="h-perf">
                    <CardHeader
                      title="Dashboard API performance"
                      titleId="h-perf"
                      meta={perf.timestamp ? `Last run ${new Date(perf.timestamp + 'Z').toLocaleTimeString()}` : undefined}
                    />
                    <div className="mb-4 flex items-end gap-3">
                      <BigNumber size="sm" value={perf.total_ms} unit="ms" />
                      {slow
                        ? <StatusPill status={slow} size="sm">{slow === 'down' ? 'Very slow' : 'Slow'}</StatusPill>
                        : <Badge>Fast</Badge>}
                    </div>
                    <ul className="space-y-1.5">
                      {perf.sections.filter((s) => s.name !== 'init' && s.name !== 'done').map((s) => {
                        const pct = perf.total_ms > 0 ? (s.ms / perf.total_ms) * 100 : 0;
                        const fill = s.ms >= 1000 ? 'bg-down' : s.ms >= 200 ? 'bg-degraded' : 'bg-accent';
                        return (
                          <li key={s.name} className="flex items-center gap-2 text-meta">
                            <span className="w-32 shrink-0 truncate font-mono text-fg-2">{s.name}</span>
                            <span aria-hidden="true" className="h-[6px] flex-1 overflow-hidden rounded-pill bg-surface-3">
                              <span className={cn('block h-full rounded-pill', fill)} style={{ width: `${Math.max(1, pct)}%` }} />
                            </span>
                            <span className="num w-16 shrink-0 text-right text-fg-2">{s.ms} ms</span>
                          </li>
                        );
                      })}
                    </ul>
                  </Card>
                );
              })()}
            </div>
          );
        }}
      </QueryState>
      {ConfirmDialogElement}
    </div>
  );
}
