'use client';

import { Card, CardHeader } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { StatusPill } from '@/components/ui/StatusPill';
import { Table, THead, TBody, Tr, Th, Td } from '@/components/ui/Table';
import type { HealthState } from '@/lib/status';
import { KV, KVGrid, StatGrid, StatTile, TableCard, formatBytes, formatDate, isNum } from './parts';

interface K8sNode {
  name: string;
  ready: boolean | null;
  state: string;
  ready_reason?: string;
  pressure: string[];
  roles: string[];
  unschedulable?: boolean;
  kubelet_version?: string;
  os_image?: string;
  cpu_cores?: number | null;
  memory_bytes?: number | null;
  pods_capacity?: number | null;
}

interface K8sIssue {
  kind: string;
  detail?: string;
}

interface K8sProblemPod {
  namespace: string;
  name: string;
  node?: string;
  phase?: string;
  created?: string;
  issues: K8sIssue[];
  restarts?: number | null;
  last_restart?: string;
}

interface K8sProblemWorkload {
  kind: string;
  namespace: string;
  name: string;
  desired: number | null;
  ready: number | null;
  rolling?: boolean;
  stalled?: boolean;
  reason?: string;
  problem: boolean;
}

interface K8sPvc {
  namespace: string;
  name: string;
  phase?: string;
  storage_class?: string;
  created?: string;
}

interface K8sNamespace {
  name: string;
  pods: number;
  running: number;
  pending: number;
  succeeded: number;
  failed: number;
  problem_pods: number;
  workloads: number;
  workloads_degraded: number;
  pvcs: number;
  pvcs_pending: number;
}

interface K8sWorkloadTotals {
  total: number;
  problem: number;
}

interface KubernetesData {
  collected_at?: string;
  version?: { git_version?: string; platform?: string };
  namespace_filter?: string;
  nodes?: K8sNode[] | null;
  node_totals?: { total: number; ready: number; not_ready: number; unknown: number; pressure: number } | null;
  pod_totals?: { total: number; running: number; pending: number; succeeded: number; failed: number; unknown: number; problem: number } | null;
  workload_totals?: Partial<Record<'deployments' | 'statefulsets' | 'daemonsets', K8sWorkloadTotals | null>>;
  pvc_totals?: { total: number; pending: number } | null;
  namespaces?: K8sNamespace[];
  namespaces_total?: number;
  problem_pods?: K8sProblemPod[];
  problem_pods_total?: number;
  problem_workloads?: K8sProblemWorkload[];
  problem_workloads_total?: number;
  pending_pvcs?: K8sPvc[];
  pending_pvcs_total?: number;
  truncated?: Partial<Record<string, boolean>>;
  unavailable?: string[];
}

const ISSUE_LABEL: Record<string, string> = {
  crashloop: 'CrashLoopBackOff',
  imagepull: 'ImagePullBackOff',
  error: 'Error',
  failed: 'Failed',
  pending: 'Pending',
};

function nodeState(ready: boolean | null | undefined): HealthState {
  return ready === true ? 'ok' : ready === false ? 'down' : 'unknown';
}

function issueTone(kind: string): 'down' | 'warning' | 'neutral' {
  if (kind === 'crashloop') return 'down';
  if (kind === 'pending') return 'neutral';
  return 'warning';
}

export function KubernetesDetail({ data }: { data: KubernetesData }) {
  const nodes = data.nodes ?? null;
  const nodeTotals = data.node_totals ?? null;
  const podTotals = data.pod_totals ?? null;
  const pvcTotals = data.pvc_totals ?? null;
  const wl = data.workload_totals ?? {};
  const unavailable = data.unavailable ?? [];
  const truncated = Object.entries(data.truncated ?? {}).filter(([, v]) => v === true).map(([k]) => k);
  const problemPods = data.problem_pods ?? [];
  const problemWorkloads = data.problem_workloads ?? [];
  const pendingPvcs = data.pending_pvcs ?? [];
  const namespaces = data.namespaces ?? [];

  // The workload figure is only a number when all three kinds could be read.
  const kinds = [wl.deployments, wl.statefulsets, wl.daemonsets];
  const workloadsKnown = kinds.every((k) => k !== null && k !== undefined);
  const workloadsDegraded = workloadsKnown ? kinds.reduce((sum, k) => sum + (k?.problem ?? 0), 0) : null;
  const workloadsTotal = workloadsKnown ? kinds.reduce((sum, k) => sum + (k?.total ?? 0), 0) : null;

  const nodesValue = nodeTotals ? `${nodeTotals.ready}/${nodeTotals.total}` : null;
  const podsValue = podTotals ? `${podTotals.running}/${podTotals.total}` : null;

  return (
    <div className="space-y-6">
      {unavailable.length > 0 && (
        <Card as="section" padding="sm">
          <p className="text-ui text-fg-2">
            Could not read: <span className="text-fg">{unavailable.join(', ')}</span>. These values show as unknown.
          </p>
        </Card>
      )}

      {truncated.length > 0 && (
        <Card as="section" padding="sm">
          <p className="text-ui text-fg-2">
            Page cap reached for <span className="text-fg">{truncated.join(', ')}</span>. Counts for these are partial.
          </p>
        </Card>
      )}

      <StatGrid cols={5}>
        <StatTile
          label="Nodes ready"
          value={nodesValue}
          state={nodeTotals ? (nodeTotals.not_ready > 0 ? 'down' : nodeTotals.unknown > 0 ? 'unknown' : undefined) : undefined}
        />
        <StatTile label="Pods running" value={podsValue} />
        <StatTile
          label="Problem pods"
          value={podTotals ? podTotals.problem : null}
          state={podTotals && podTotals.problem > 0 ? 'warning' : undefined}
        />
        <StatTile
          label="Workloads degraded"
          value={workloadsDegraded}
          unit={isNum(workloadsTotal) ? `of ${workloadsTotal}` : undefined}
          state={isNum(workloadsDegraded) && workloadsDegraded > 0 ? 'warning' : undefined}
        />
        <StatTile
          label="Pending volume claims"
          value={pvcTotals ? pvcTotals.pending : null}
          state={pvcTotals && pvcTotals.pending > 0 ? 'warning' : undefined}
        />
      </StatGrid>

      {/* Nodes */}
      {nodes && nodes.length > 0 && (
        <TableCard title="Nodes" meta={`${nodes.length}`}>
          <Table>
            <THead>
              <Tr>
                <Th>Name</Th>
                <Th>State</Th>
                <Th>Roles</Th>
                <Th>Pressure</Th>
                <Th>Kubelet</Th>
                <Th numeric>CPU</Th>
                <Th numeric>Memory</Th>
                <Th numeric>Pod capacity</Th>
              </Tr>
            </THead>
            <TBody>
              {nodes.map((n) => (
                <Tr key={n.name}>
                  <Td className="font-mono">{n.name}</Td>
                  <Td>
                    <StatusPill status={nodeState(n.ready)} size="sm">{n.state || undefined}</StatusPill>
                    {n.unschedulable && <Badge className="ml-1.5">Cordoned</Badge>}
                  </Td>
                  <Td muted>{n.roles && n.roles.length > 0 ? n.roles.join(', ') : '—'}</Td>
                  <Td>
                    {n.pressure && n.pressure.length > 0
                      ? n.pressure.map((p) => <Badge key={p} tone="warning" className="mr-1">{p}</Badge>)
                      : <span className="text-fg-3">—</span>}
                  </Td>
                  <Td muted className="whitespace-nowrap font-mono text-meta">{n.kubelet_version || '—'}</Td>
                  <Td numeric muted>{isNum(n.cpu_cores) ? `${n.cpu_cores} cores` : '—'}</Td>
                  <Td numeric muted className="whitespace-nowrap">{formatBytes(n.memory_bytes) ?? '—'}</Td>
                  <Td numeric muted>{isNum(n.pods_capacity) ? n.pods_capacity : '—'}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      )}

      {/* Problem pods */}
      {problemPods.length > 0 && (
        <TableCard
          title="Pods needing attention"
          meta={isNum(data.problem_pods_total) && data.problem_pods_total > problemPods.length
            ? `${problemPods.length} of ${data.problem_pods_total}`
            : `${problemPods.length}`}
        >
          <Table>
            <THead>
              <Tr>
                <Th>Pod</Th>
                <Th>Problem</Th>
                <Th>Node</Th>
                <Th numeric>Restarts</Th>
                <Th numeric>Created</Th>
              </Tr>
            </THead>
            <TBody>
              {problemPods.map((p) => (
                <Tr key={`${p.namespace}/${p.name}`}>
                  <Td className="max-w-[320px] truncate font-mono">
                    <span className="text-fg-3">{p.namespace}/</span>{p.name}
                  </Td>
                  <Td>
                    {p.issues && p.issues.length > 0 ? p.issues.map((i, idx) => (
                      <Badge key={idx} tone={issueTone(i.kind)} className="mr-1" title={i.detail || undefined}>
                        {ISSUE_LABEL[i.kind] ?? i.kind}
                      </Badge>
                    )) : <span className="text-fg-3">Restarting ({p.phase || 'unknown'})</span>}
                  </Td>
                  <Td muted className="font-mono text-meta">{p.node || '—'}</Td>
                  <Td numeric>{isNum(p.restarts) ? p.restarts : '—'}</Td>
                  <Td numeric muted className="whitespace-nowrap text-meta">{formatDate(p.created) ?? '—'}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      )}

      {/* Problem workloads */}
      {problemWorkloads.length > 0 && (
        <TableCard
          title="Workloads not fully ready"
          meta={isNum(data.problem_workloads_total) && data.problem_workloads_total > problemWorkloads.length
            ? `${problemWorkloads.length} of ${data.problem_workloads_total}`
            : `${problemWorkloads.length}`}
        >
          <Table>
            <THead>
              <Tr>
                <Th>Workload</Th>
                <Th>Kind</Th>
                <Th numeric>Ready</Th>
                <Th>Note</Th>
              </Tr>
            </THead>
            <TBody>
              {problemWorkloads.map((w) => (
                <Tr key={`${w.kind}/${w.namespace}/${w.name}`}>
                  <Td className="max-w-[320px] truncate font-mono">
                    <span className="text-fg-3">{w.namespace}/</span>{w.name}
                  </Td>
                  <Td muted>{w.kind}</Td>
                  <Td numeric className={w.ready === 0 ? 'text-down' : 'text-warning'}>
                    {isNum(w.ready) && isNum(w.desired) ? `${w.ready}/${w.desired}` : '—'}
                  </Td>
                  <Td muted className="text-meta">{w.reason || (w.stalled ? 'Rollout stalled' : '—')}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      )}

      {/* Pending PVCs */}
      {pendingPvcs.length > 0 && (
        <TableCard title="Pending volume claims" meta={`${pendingPvcs.length}`}>
          <Table>
            <THead>
              <Tr>
                <Th>Claim</Th>
                <Th>Storage class</Th>
                <Th numeric>Created</Th>
              </Tr>
            </THead>
            <TBody>
              {pendingPvcs.map((c) => (
                <Tr key={`${c.namespace}/${c.name}`}>
                  <Td className="max-w-[320px] truncate font-mono">
                    <span className="text-fg-3">{c.namespace}/</span>{c.name}
                  </Td>
                  <Td muted>{c.storage_class || '—'}</Td>
                  <Td numeric muted className="whitespace-nowrap text-meta">{formatDate(c.created) ?? '—'}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      )}

      {/* Namespaces */}
      {namespaces.length > 0 && (
        <TableCard
          title="Namespaces"
          meta={isNum(data.namespaces_total) && data.namespaces_total > namespaces.length
            ? `${namespaces.length} of ${data.namespaces_total}`
            : `${namespaces.length}`}
          maxHeight={420}
        >
          <Table>
            <THead sticky>
              <Tr>
                <Th>Namespace</Th>
                <Th numeric>Pods</Th>
                <Th numeric>Running</Th>
                <Th numeric>Pending</Th>
                <Th numeric>Failed</Th>
                <Th numeric>Workloads</Th>
                <Th numeric>Volume claims</Th>
              </Tr>
            </THead>
            <TBody>
              {namespaces.map((ns) => (
                <Tr key={ns.name}>
                  <Td className="font-mono">{ns.name}</Td>
                  <Td numeric>{isNum(ns.pods) ? ns.pods : '—'}</Td>
                  <Td numeric muted>{isNum(ns.running) ? ns.running : '—'}</Td>
                  <Td numeric className={isNum(ns.pending) && ns.pending > 0 ? 'text-warning' : 'text-fg-3'}>
                    {isNum(ns.pending) ? ns.pending : '—'}
                  </Td>
                  <Td numeric className={isNum(ns.failed) && ns.failed > 0 ? 'text-down' : 'text-fg-3'}>
                    {isNum(ns.failed) ? ns.failed : '—'}
                  </Td>
                  <Td numeric muted>
                    {isNum(ns.workloads) ? ns.workloads : '—'}
                    {isNum(ns.workloads_degraded) && ns.workloads_degraded > 0 && (
                      <span className="ml-1 text-warning">({ns.workloads_degraded} degraded)</span>
                    )}
                  </Td>
                  <Td numeric muted>{isNum(ns.pvcs) ? ns.pvcs : '—'}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TableCard>
      )}

      <Card as="section">
        <CardHeader title="Cluster" />
        <KVGrid>
          <KV label="Version" mono>{data.version?.git_version}</KV>
          <KV label="Platform">{data.version?.platform}</KV>
          <KV label="Namespace filter" mono>{data.namespace_filter || 'All namespaces'}</KV>
          <KV label="Collected">{formatDate(data.collected_at)}</KV>
        </KVGrid>
      </Card>
    </div>
  );
}
