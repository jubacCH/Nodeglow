'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import { CheckCircle2, ChevronDown, ChevronRight, Eye, RefreshCw, ThumbsDown, ThumbsUp } from 'lucide-react';
import { Breadcrumbs } from '@/components/layout/Breadcrumbs';
import { PageHeader } from '@/components/layout/PageHeader';
import { AffectedHostsCard } from '@/components/incidents/AffectedHostsCard';
import { IncidentStatusBadge, SeverityIndicator } from '@/components/incidents/IncidentBits';
import { Badge } from '@/components/ui/Badge';
import { Button, buttonClasses } from '@/components/ui/Button';
import { Card, CardHeader } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryState } from '@/components/ui/QueryState';
import { Skeleton } from '@/components/ui/Skeleton';
import { StatusDot } from '@/components/ui/StatusDot';
import { Table, TableContainer, TBody, Td, Th, THead, Tr } from '@/components/ui/Table';
import { Tag } from '@/components/ui/Tag';
import { aiUnavailableMessage, useAiStatus, type AiStatus } from '@/hooks/queries/useAiStatus';
import { useIncidentAction } from '@/hooks/queries/useAlerts';
import { apiErrorMessage, get, post } from '@/lib/api';
import {
  SEVERITY_LABEL, formatDateTime, formatDuration, incidentDurationMs, incidentNeedsGlow, parseServerDate, ruleLabel,
  severityState, type IncidentItem,
} from '@/lib/incidents';
import { cn } from '@/lib/utils';
import { useAuthStore, useIsEditor } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';

const SYSLOG_SEVERITY: Record<number, { label: string; tone: 'down' | 'warning' }> = {
  0: { label: 'EMERG', tone: 'down' },
  1: { label: 'ALERT', tone: 'down' },
  2: { label: 'CRIT', tone: 'down' },
  3: { label: 'ERROR', tone: 'down' },
  4: { label: 'WARN', tone: 'warning' },
};

function SyslogSeverity({ severity }: { severity: number }) {
  const info = SYSLOG_SEVERITY[severity];
  return <Badge tone={info?.tone ?? 'neutral'} className="font-mono">{info?.label ?? `SEV${severity}`}</Badge>;
}

interface RelatedLog {
  timestamp: string;
  hostname: string;
  severity: number;
  app_name: string;
  message: string;
  template_hash?: string;
}

interface PatternInfo {
  count: number;
  template: string;
  example: string;
  hosts: { name: string; count: number }[];
  apps: { name: string; count: number }[];
  severity_breakdown: Record<string, number>;
  first_seen: string;
  last_seen: string;
  is_known: boolean;
  noise_score: number | null;
  tags: string[];
  avg_rate_per_hour: number | null;
}

interface LogAnalysis {
  summary: string;
  total_messages: number;
  unique_patterns: number;
  affected_hosts: number;
  single_source: boolean;
  worst_severity: string;
  patterns: PatternInfo[];
  top_hosts: { name: string; count: number }[];
  precursor_hints: {
    template: string;
    precedes: string;
    confidence: number;
    lead_time_min: number | null;
  }[];
}

/** Timeline entry as GET /api/v1/incidents/{id} returns it. */
interface TimelineEvent {
  timestamp: string;
  /** Current field name; `event_type` kept for older backends. */
  type?: string;
  event_type?: string;
  summary: string;
  detail: string | null;
}

interface IncidentDetail extends IncidentItem {
  events: TimelineEvent[];
  events_total?: number;
  related_logs?: RelatedLog[];
  log_analysis?: LogAnalysis | null;
  postmortem?: string | null;
  postmortem_generated_at?: string | null;
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-meta text-fg-3">{label}</dt>
      <dd className="mt-0.5 truncate text-ui text-fg">{children}</dd>
    </div>
  );
}

const EVENT_LABEL: Record<string, string> = {
  created: 'Opened', opened: 'Opened', acknowledged: 'Acknowledged', resolved: 'Resolved',
  update: 'Update', updated: 'Update', escalated: 'Escalated', reopened: 'Reopened',
};

function eventLabel(t: string | undefined) {
  if (!t) return 'Event';
  return EVENT_LABEL[t] ?? ruleLabel(t);
}

export default function IncidentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const incidentId = Number(id);
  const toast = useToastStore((s) => s.show);
  const canEdit = useIsEditor();
  const { data: aiStatus } = useAiStatus();
  // No AI, no postmortem on its way: stop polling for one.
  const aiAvailable = aiStatus?.available ?? true;
  const action = useIncidentAction();
  const [feedbackBusy, setFeedbackBusy] = useState(false);

  const query = useQuery({
    queryKey: ['incident', incidentId],
    queryFn: () => get<IncidentDetail>(`/api/v1/incidents/${incidentId}`),
    enabled: incidentId > 0,
    refetchInterval: (q) => {
      const d = q.state.data;
      if (aiAvailable && d && d.status === 'resolved' && !d.postmortem) return 5000;
      return false;
    },
  });
  const data = query.data;

  useEffect(() => {
    document.title = `${data?.title ?? `Incident #${incidentId}`} | Nodeglow`;
  }, [data?.title, incidentId]);

  async function submitFeedback(verdict: 'real' | 'noise') {
    setFeedbackBusy(true);
    try {
      await post(`/api/v1/incidents/${incidentId}/feedback`, { verdict });
      await query.refetch();
      toast(verdict === 'noise' ? 'Marked as noise — this pattern is suppressed' : 'Marked as a real problem', 'success');
    } catch (e) {
      toast(apiErrorMessage(e, 'Could not save the feedback'), 'error');
    } finally {
      setFeedbackBusy(false);
    }
  }

  const pending = action.isPending;
  const title = data?.title ?? `Incident #${incidentId}`;
  const durationMs = data ? incidentDurationMs(data) : null;

  return (
    <div>
      <Breadcrumbs items={[{ label: 'Incidents', href: '/alerts' }, { label: title }]} />
      <PageHeader
        title={title}
        status={data && severityState(data.severity) ? (
          <StatusDot
            status={severityState(data.severity)!}
            size="lg"
            glow={incidentNeedsGlow(data)}
            breathe={incidentNeedsGlow(data)}
            label={`${SEVERITY_LABEL[data.severity]} severity`}
          />
        ) : undefined}
        description={data ? (
          <>
            <span title={data.rule}>{ruleLabel(data.rule)}</span>
            {' · '}{data.status === 'resolved' ? 'lasted' : 'open for'} {durationMs === null ? '—' : formatDuration(durationMs)}
          </>
        ) : undefined}
        actions={data && canEdit && data.status !== 'resolved' ? (
          <>
            {data.status === 'open' && (
              <Button variant="secondary" disabled={pending} onClick={() => action.mutate({ id: incidentId, action: 'acknowledge' })}>
                <Eye size={15} aria-hidden="true" /> Acknowledge
              </Button>
            )}
            <Button disabled={pending} onClick={() => action.mutate({ id: incidentId, action: 'resolve' })}>
              <CheckCircle2 size={15} aria-hidden="true" /> Resolve
            </Button>
          </>
        ) : undefined}
      />

      <QueryState<IncidentDetail>
        query={query}
        errorTitle="Could not load the incident"
        isEmpty={() => false}
        loading={
          <div className="grid grid-cols-1 gap-4 min-[1200px]:grid-cols-3" aria-busy="true" aria-label="Loading">
            <Skeleton className="h-40 w-full min-[1200px]:col-span-2" />
            <Skeleton className="h-40 w-full" />
          </div>
        }
      >
        {(inc) => (
          <div className="grid grid-cols-1 gap-4 min-[1200px]:grid-cols-3">
            <div className="min-w-0 space-y-4 min-[1200px]:col-span-2">
              <Card
                as="section"
                aria-labelledby="inc-overview"
              >
                <CardHeader title="Overview" titleId="inc-overview" actions={<IncidentStatusBadge status={inc.status} />} />
                {inc.summary && <p className="mb-4 text-ui text-fg-2">{inc.summary}</p>}
                <dl className="grid grid-cols-2 gap-x-4 gap-y-3 min-[760px]:grid-cols-4">
                  <Fact label="Severity"><SeverityIndicator severity={inc.severity} status={inc.status} glow={false} /></Fact>
                  <Fact label="Rule"><span className="font-mono text-meta" title={inc.rule}>{inc.rule}</span></Fact>
                  <Fact label="Opened"><span title={inc.created_at}>{formatDateTime(inc.created_at)}</span></Fact>
                  <Fact label={inc.status === 'resolved' ? 'Duration' : 'Open for'}>
                    <span className="num">{durationMs === null ? '—' : formatDuration(durationMs)}</span>
                  </Fact>
                  <Fact label="Last update">{formatDateTime(inc.updated_at)}</Fact>
                  <Fact label="Acknowledged by">{inc.acknowledged_by ?? <span className="text-fg-3">—</span>}</Fact>
                  <Fact label="Resolved">{inc.resolved_at ? formatDateTime(inc.resolved_at) : <span className="text-fg-3">Not yet</span>}</Fact>
                  <Fact label="ID"><span className="num">#{inc.id}</span></Fact>
                </dl>

                <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border pt-4">
                  <span id="feedback-label" className="mr-1 text-meta text-fg-2">Was this alert useful?</span>
                  <div role="group" aria-labelledby="feedback-label" className="flex gap-1.5">
                    <Button
                      size="sm"
                      variant="secondary"
                      aria-pressed={inc.feedback === 'real'}
                      disabled={feedbackBusy || !canEdit}
                      onClick={() => submitFeedback('real')}
                      className={cn(inc.feedback === 'real' && 'border-ok/40 bg-ok-soft text-ok hover:bg-ok-soft')}
                    >
                      <ThumbsUp size={13} aria-hidden="true" /> Real problem
                    </Button>
                    <Button
                      size="sm"
                      variant="secondary"
                      aria-pressed={inc.feedback === 'noise'}
                      disabled={feedbackBusy || !canEdit}
                      onClick={() => submitFeedback('noise')}
                      className={cn(inc.feedback === 'noise' && 'border-border-2 bg-surface-3 text-fg')}
                    >
                      <ThumbsDown size={13} aria-hidden="true" /> Noise
                    </Button>
                  </div>
                  {inc.feedback_by && (
                    <span className="text-meta text-fg-3">
                      by {inc.feedback_by}{inc.feedback_at ? ` · ${formatDateTime(inc.feedback_at)}` : ''}
                    </span>
                  )}
                </div>
              </Card>

              <TimelineCard events={inc.events ?? []} total={inc.events_total} />

              {inc.status === 'resolved' && (
                <PostmortemSection
                  incidentId={incidentId}
                  postmortem={inc.postmortem}
                  generatedAt={inc.postmortem_generated_at}
                  onRegenerate={() => query.refetch()}
                  aiStatus={aiStatus}
                />
              )}

              {inc.log_analysis && <LogAnalysisSection analysis={inc.log_analysis} />}
              {inc.related_logs && inc.related_logs.length > 0 && <RawLogsSection logs={inc.related_logs} />}
            </div>

            <div className="min-w-0 space-y-4">
              <AffectedHostsCard incident={inc} />
            </div>
          </div>
        )}
      </QueryState>
    </div>
  );
}

/* ---------- Timeline ---------- */

function TimelineCard({ events, total }: { events: TimelineEvent[]; total?: number }) {
  // The API returns the newest events (capped); show newest first.
  const sorted = [...events].sort((a, b) => (a.timestamp < b.timestamp ? 1 : a.timestamp > b.timestamp ? -1 : 0));
  const more = (total ?? 0) > events.length;
  return (
    <Card as="section" aria-labelledby="inc-timeline">
      <CardHeader title="Timeline" titleId="inc-timeline" meta={more ? `latest ${events.length} of ${total}` : events.length ? `${events.length}` : undefined} />
      {sorted.length === 0 ? (
        <p className="text-ui text-fg-3">No events recorded.</p>
      ) : (
        <ol className="relative space-y-4">
          {sorted.map((evt, i) => {
            const t = evt.type ?? evt.event_type;
            return (
              <li key={`${evt.timestamp}-${i}`} className="relative flex gap-3">
                {i < sorted.length - 1 && <span aria-hidden="true" className="absolute bottom-[-16px] left-[4px] top-4 w-px bg-border-2" />}
                <span aria-hidden="true" className={cn('mt-[5px] h-[9px] w-[9px] shrink-0 rounded-full border-2', t === 'resolved' ? 'border-ok bg-ok' : 'border-line bg-surface')} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <span className="text-ui font-medium text-fg">{eventLabel(t)}</span>
                    <time dateTime={evt.timestamp} className="text-meta text-fg-3">{formatDateTime(evt.timestamp)}</time>
                  </div>
                  <p className="mt-0.5 text-ui text-fg-2 [overflow-wrap:anywhere]">{evt.summary}</p>
                  {evt.detail && (
                    <pre className="mt-1.5 max-h-48 overflow-auto rounded-ctl border border-border bg-surface-2 p-2 font-mono text-meta text-fg-2">{evt.detail}</pre>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </Card>
  );
}

/* ---------- Postmortem ---------- */

function PostmortemSection({
  incidentId, postmortem, generatedAt, onRegenerate, aiStatus,
}: {
  incidentId: number;
  postmortem?: string | null;
  generatedAt?: string | null;
  onRegenerate: () => void;
  aiStatus?: AiStatus;
}) {
  const toast = useToastStore((s) => s.show);
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin');
  const canEdit = useIsEditor();
  const [regenerating, setRegenerating] = useState(false);
  const aiUnavailable = aiStatus ? !aiStatus.available : false;

  async function handleRegenerate() {
    setRegenerating(true);
    try {
      await post(`/api/v1/incidents/${incidentId}/postmortem`);
      toast('Postmortem generation started', 'success');
      setTimeout(onRegenerate, 3000);
    } catch (e) {
      toast(apiErrorMessage(e, 'Could not start the postmortem'), 'error');
    } finally {
      setRegenerating(false);
    }
  }

  const isFailed = postmortem?.startsWith('[Generation failed]');
  const regen = (label: string) => canEdit && (
    <Button size="sm" variant="secondary" onClick={handleRegenerate} disabled={regenerating || aiUnavailable}
      title={aiUnavailable ? 'AI features are off (Settings → AI)' : undefined}>
      <RefreshCw size={13} aria-hidden="true" className={regenerating ? 'animate-spin' : ''} /> {label}
    </Button>
  );

  return (
    <Card as="section" aria-labelledby="inc-postmortem">
      <CardHeader
        title="Postmortem"
        titleId="inc-postmortem"
        meta={generatedAt && !isFailed ? `Generated ${formatDateTime(generatedAt)}` : undefined}
        actions={postmortem ? regen(isFailed ? 'Retry' : 'Regenerate') : undefined}
      />
      {aiUnavailable && !postmortem ? (
        <p className="text-ui text-fg-2" data-testid="postmortem-ai-disabled">
          {aiUnavailableMessage(aiStatus, isAdmin)}{' '}
          {isAdmin && <Link href="/settings?tab=ai" className="text-accent hover:underline">Open AI settings</Link>}
        </p>
      ) : postmortem && !isFailed ? (
        <div className="whitespace-pre-wrap text-ui leading-relaxed text-fg">{postmortem}</div>
      ) : isFailed ? (
        <p role="alert" className="rounded-ctl border border-warning/30 bg-warning-soft px-3 py-2 text-ui text-warning">{postmortem}</p>
      ) : (
        <p className="flex items-center gap-2 text-ui text-fg-2" role="status">
          <RefreshCw size={14} aria-hidden="true" className="animate-spin text-fg-3" /> Generating the postmortem…
        </p>
      )}
    </Card>
  );
}

/* ---------- Log analysis ---------- */

function LogAnalysisSection({ analysis }: { analysis: LogAnalysis }) {
  return (
    <Card as="section" aria-labelledby="inc-analysis">
      <CardHeader
        title="Error analysis"
        titleId="inc-analysis"
        actions={
          <span className="flex flex-wrap gap-1.5">
            <Tag>{analysis.total_messages} messages</Tag>
            <Tag>{analysis.unique_patterns} pattern{analysis.unique_patterns !== 1 ? 's' : ''}</Tag>
            <Tag>{analysis.affected_hosts} host{analysis.affected_hosts !== 1 ? 's' : ''}</Tag>
          </span>
        }
      />
      <p className="mb-4 text-ui leading-relaxed text-fg">{analysis.summary}</p>

      {analysis.precursor_hints.length > 0 && (
        <div className="mb-4 rounded-ctl border border-warning/30 bg-warning-soft px-3 py-2.5">
          <p className="mb-1.5 text-meta font-medium text-warning">Precursor pattern detected</p>
          <ul className="space-y-1">
            {analysis.precursor_hints.map((hint, i) => (
              <li key={i} className="flex items-start gap-2 text-meta text-fg">
                <span className="num shrink-0 font-medium text-warning">{hint.confidence}%</span>
                <span>
                  This pattern has preceded <span className="font-medium">{hint.precedes}</span> events
                  {hint.lead_time_min != null && <span className="text-fg-2"> (~{hint.lead_time_min} min lead time)</span>}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <ul className="space-y-2">
        {analysis.patterns.map((pattern, i) => (
          <li key={i} className="rounded-ctl border border-border p-3">
            <div className="flex items-start gap-3">
              <span className="num w-10 shrink-0 text-right font-display text-lead font-medium text-fg">{pattern.count}×</span>
              <div className="min-w-0 flex-1">
                <p className="break-all font-mono text-meta leading-relaxed text-fg">{pattern.template}</p>
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  {!pattern.is_known && <Badge tone="warning">New pattern</Badge>}
                  {pattern.noise_score != null && pattern.noise_score >= 70 && <Badge>noise {pattern.noise_score}%</Badge>}
                  {pattern.tags.filter(Boolean).map((tag) => <Tag key={tag}>{tag}</Tag>)}
                  {Object.entries(pattern.severity_breakdown).map(([sev, count]) => (
                    <span key={sev} className="text-micro text-fg-3">{sev}: {count}</span>
                  ))}
                </div>
                {pattern.hosts.length > 0 && (
                  <p className="mt-1.5 text-micro text-fg-3">
                    Hosts:{' '}
                    {pattern.hosts.map((h, j) => (
                      <span key={h.name} className="font-mono text-fg-2">{j > 0 && ', '}{h.name}{h.count > 1 ? ` (${h.count})` : ''}</span>
                    ))}
                  </p>
                )}
              </div>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

/* ---------- Raw logs (collapsible) ---------- */

function logTime(ts: string) {
  const d = parseServerDate(ts);
  return d ? d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : ts;
}

function RawLogsSection({ logs }: { logs: RelatedLog[] }) {
  const [expanded, setExpanded] = useState(false);
  const Icon = expanded ? ChevronDown : ChevronRight;
  return (
    <Card as="section" aria-labelledby="inc-logs">
      <h2 id="inc-logs" className="text-body font-medium text-fg">
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls="inc-logs-table"
          onClick={() => setExpanded(!expanded)}
          className={buttonClasses({ variant: 'ghost', size: 'sm', className: '-ml-2 text-body text-fg' })}
        >
          <Icon size={14} aria-hidden="true" /> Related syslog messages
          <span className="num text-meta font-normal text-fg-3">({logs.length})</span>
        </button>
      </h2>
      {expanded && (
        <div id="inc-logs-table" className="mt-3">
          {logs.length === 0 ? (
            <EmptyState compact variant="no-results" title="No related messages" />
          ) : (
            <TableContainer maxHeight={480}>
              <Table density="compact" aria-label="Related syslog messages">
                <THead sticky>
                  <Tr>
                    <Th>Time</Th>
                    <Th>Severity</Th>
                    <Th>Host</Th>
                    <Th>App</Th>
                    <Th>Message</Th>
                  </Tr>
                </THead>
                <TBody>
                  {logs.map((log, i) => (
                    <Tr key={i}>
                      <Td muted className="whitespace-nowrap font-mono text-meta">{logTime(log.timestamp)}</Td>
                      <Td><SyslogSeverity severity={log.severity} /></Td>
                      <Td className="whitespace-nowrap font-mono text-meta">{log.hostname}</Td>
                      <Td muted className="whitespace-nowrap text-meta">{log.app_name || '—'}</Td>
                      <Td className="min-w-[280px] break-all py-1 font-mono text-meta">{log.message}</Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </TableContainer>
          )}
        </div>
      )}
    </Card>
  );
}
