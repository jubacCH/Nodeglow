'use client';

import { Card, CardHeader } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import type { HealthState } from '@/lib/status';
import { KV, KVGrid, SectionTitle, StatGrid, StatTile, StateLabel, TableCard, formatBytes, formatCount, formatDate, isNum } from './parts';

/** Sample values are numbers; NaN and infinities are stored as strings. */
type SampleValue = number | string;

interface SeriesRow {
  series: string;
  name: string;
  labels: Record<string, string>;
  value: SampleValue;
}

interface PromRule {
  line: number;
  severity: 'critical' | 'warning' | 'info';
  expr: string;
  title: string;
  matched: number;
  /** Series of the rule's metric regardless of labels (absent in older snapshots). */
  name_seen?: number;
  nan: number;
  firing: number;
  firing_series: SeriesRow[];
  sample_series: SeriesRow[];
}

interface PromFamily {
  name: string;
  type: string;
  help: string;
  samples: number;
}

interface PrometheusData {
  collected_at?: string;
  scrape?: {
    bytes?: number;
    duration_ms?: number;
    format?: string;
    series?: number;
    families?: number;
  };
  families?: PromFamily[];
  families_truncated?: number;
  preview?: SeriesRow[];
  preview_truncated?: boolean;
  rules?: PromRule[];
  rule_errors?: { line: number; text: string; error: string }[];
  unavailable?: string[];
}

function fmtValue(v: SampleValue | null | undefined): string {
  if (typeof v === 'string') return v;
  if (!isNum(v)) return '—';
  return Number.isInteger(v) ? String(v) : Number(v.toPrecision(6)).toString();
}

function labelText(labels: Record<string, string> | undefined): string {
  const entries = Object.entries(labels ?? {});
  return entries.length === 0 ? '' : entries.map(([k, v]) => `${k}="${v}"`).join(', ');
}

/** The metric exists, but no series carries the rule's labels (yet): the rule is fine, it just has nothing to fire on. */
function idle(rule: PromRule): boolean {
  return rule.matched === 0 && isNum(rule.name_seen) && rule.name_seen > 0;
}

function ruleState(rule: PromRule): HealthState {
  if (idle(rule)) return 'ok';
  if (!isNum(rule.matched) || rule.matched === 0) return 'unknown';
  if (rule.firing > 0) return rule.severity === 'info' ? 'warning' : rule.severity === 'critical' ? 'down' : 'warning';
  return 'ok';
}

function ruleLabel(rule: PromRule): string {
  if (idle(rule)) return 'OK · no series with these labels';
  if (!isNum(rule.matched) || rule.matched === 0) return 'Metric not found';
  if (rule.firing > 0) return `${formatCount(rule.firing)} firing`;
  return 'OK';
}

function SeriesTable({ title, rows, meta }: { title: string; rows: SeriesRow[]; meta?: string }) {
  return (
    <TableCard title={title} meta={meta ?? `${rows.length}`} maxHeight={360}>
      <Table>
        <THead>
          <Tr>
            <Th>Metric</Th>
            <Th>Labels</Th>
            <Th numeric>Value</Th>
          </Tr>
        </THead>
        <TBody>
          {rows.map((r, i) => (
            <Tr key={`${r.series}-${i}`}>
              <Td className="font-mono">{r.name}</Td>
              <Td muted className="font-mono text-meta wrap-break-word">{labelText(r.labels) || '—'}</Td>
              <Td numeric className="whitespace-nowrap font-mono">{fmtValue(r.value)}</Td>
            </Tr>
          ))}
        </TBody>
      </Table>
    </TableCard>
  );
}

export function PrometheusDetail({ data }: { data: PrometheusData }) {
  const scrape = data.scrape ?? {};
  const rules = data.rules ?? [];
  const ruleErrors = data.rule_errors ?? [];
  const families = data.families ?? [];
  const preview = data.preview ?? [];
  const unavailable = data.unavailable ?? [];

  return (
    <div className="space-y-6">
      <StatGrid>
        <StatTile label="Series" value={formatCount(scrape.series)} />
        <StatTile label="Metric families" value={formatCount(scrape.families)} />
        <StatTile label="Scrape size" value={formatBytes(scrape.bytes)} />
        <StatTile label="Scrape time" value={isNum(scrape.duration_ms) ? scrape.duration_ms : null} unit={isNum(scrape.duration_ms) ? 'ms' : undefined} />
      </StatGrid>

      {/* Rules */}
      {(rules.length > 0 || ruleErrors.length > 0) && (
        <section>
          <SectionTitle meta={`${rules.length} active`}>Alert rules</SectionTitle>
          <div className="space-y-4">
            {rules.map((rule) => (
              <Card key={rule.expr} padding="sm" className="space-y-3">
                <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0">
                    <p className="wrap-break-word font-mono text-ui text-fg">{rule.expr}</p>
                    {rule.title && <p className="text-meta text-fg-3">{rule.title}</p>}
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <Badge variant="severity" severity={rule.severity}>{rule.severity}</Badge>
                    <StateLabel status={ruleState(rule)}>{ruleLabel(rule)}</StateLabel>
                  </div>
                </div>
                <p className="text-meta text-fg-3">
                  {isNum(rule.matched) ? `${rule.matched} matching series` : 'No evaluation result'}
                  {isNum(rule.nan) && rule.nan > 0 ? `, ${rule.nan} report NaN` : ''}
                </p>
                {rule.firing_series && rule.firing_series.length > 0 && (
                  <ul className="space-y-1">
                    {rule.firing_series.map((r, i) => (
                      <li key={`${r.series}-${i}`} className="flex min-w-0 items-baseline justify-between gap-3 text-meta">
                        <span className="min-w-0 wrap-break-word font-mono text-fg-2">{r.series}</span>
                        <span className="num shrink-0 font-mono text-fg">{fmtValue(r.value)}</span>
                      </li>
                    ))}
                    {rule.firing > rule.firing_series.length && (
                      <li className="text-meta text-fg-3">+{rule.firing - rule.firing_series.length} more series not listed</li>
                    )}
                  </ul>
                )}
              </Card>
            ))}
            {ruleErrors.length > 0 && (
              <Card as="section">
                <CardHeader title="Malformed rules" meta={`${ruleErrors.length}`} />
                <ul className="space-y-2">
                  {ruleErrors.map((e, i) => (
                    <li key={i} className="min-w-0 text-ui">
                      <p className="wrap-break-word font-mono text-fg-2">
                        {e.line > 0 ? `Line ${e.line}: ` : ''}{e.text}
                      </p>
                      <p className="text-meta text-warning">{e.error}</p>
                    </li>
                  ))}
                </ul>
              </Card>
            )}
          </div>
        </section>
      )}

      {rules.length === 0 && ruleErrors.length === 0 && (
        <Card as="section">
          <CardHeader title="Alert rules" />
          <p className="text-ui text-fg-3">
            No rules configured. Add one per line, e.g. <span className="font-mono">warning up == 0</span>.
          </p>
        </Card>
      )}

      {/* Series preview */}
      {preview.length > 0 && (
        <SeriesTable
          title="Series preview"
          rows={preview}
          meta={data.preview_truncated && isNum(scrape.series) ? `first ${preview.length} of ${formatCount(scrape.series)}` : `${preview.length}`}
        />
      )}

      {/* Metric families */}
      {families.length > 0 && (
        <TableCard
          title="Metric families"
          meta={data.families_truncated ? `${families.length} shown, ${data.families_truncated} more` : `${families.length}`}
          maxHeight={360}
        >
          <Table>
            <THead>
              <Tr>
                <Th>Name</Th>
                <Th>Type</Th>
                <Th numeric>Series</Th>
                <Th>Help</Th>
              </Tr>
            </THead>
            <TBody>
              {families.map((f) => (
                <Tr key={f.name}>
                  <Td className="font-mono">{f.name}</Td>
                  <Td>{f.type ? <Badge>{f.type}</Badge> : '—'}</Td>
                  <Td numeric>{formatCount(f.samples) ?? '—'}</Td>
                  <Td muted className="text-meta">{f.help || '—'}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      )}

      {unavailable.length > 0 && (
        <Card as="section">
          <CardHeader title="Could not ask" meta={`${unavailable.length}`} />
          <p className="text-ui text-fg">{unavailable.join(', ')}</p>
        </Card>
      )}

      <Card as="section">
        <CardHeader title="Last scrape" />
        <KVGrid>
          <KV label="Collected">{formatDate(data.collected_at)}</KV>
          <KV label="Format">{scrape.format === 'openmetrics' ? 'OpenMetrics' : scrape.format === 'prometheus' ? 'Prometheus text' : null}</KV>
        </KVGrid>
      </Card>
    </div>
  );
}
