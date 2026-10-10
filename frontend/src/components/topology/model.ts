/**
 * Topology data model: `parent_id` edges from /api/v1/topology turned into
 * trees, per-node health and filtering. Shared by the tree view and the map.
 */
import type { HealthState } from '@/lib/status';

export interface TopoNode {
  id: number;
  name: string;
  hostname: string;
  // 'unknown' covers a host nobody is currently observing (e.g. its probe
  // went silent) — it must render distinctly, never as 'up' or 'down'.
  status: 'up' | 'down' | 'unknown';
  check_type: string;
  source: string;
  maintenance: boolean;
}

export interface TopoEdge {
  source: number;
  target: number;
}

export interface TreeNode {
  node: TopoNode;
  children: TreeNode[];
}

/** Maintenance wins over the last result; unknown is never "ok". */
export function nodeState(n: TopoNode): HealthState {
  if (n.maintenance) return 'maint';
  if (n.status === 'down') return 'down';
  if (n.status === 'up') return 'ok';
  return 'unknown';
}

export function buildTrees(nodes: TopoNode[], edges: TopoEdge[]): { trees: TreeNode[]; orphans: TopoNode[] } {
  const nodeMap = new Map<number, TopoNode>();
  for (const n of nodes) nodeMap.set(n.id, n);

  const childIds = new Set(edges.map((e) => e.target));
  const parentToChildren = new Map<number, number[]>();
  for (const e of edges) {
    if (!parentToChildren.has(e.source)) parentToChildren.set(e.source, []);
    parentToChildren.get(e.source)!.push(e.target);
  }

  const seen = new Set<number>();
  function build(n: TopoNode): TreeNode {
    seen.add(n.id);
    const cIds = parentToChildren.get(n.id) ?? [];
    const children = cIds
      .map((id) => nodeMap.get(id))
      // `seen` guards against cycles in bad parent data.
      .filter((c): c is TopoNode => !!c && !seen.has(c.id))
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((c) => build(c));
    return { node: n, children };
  }

  const roots = nodes.filter((n) => !childIds.has(n.id));
  const withChildren = roots.filter((r) => (parentToChildren.get(r.id) ?? []).length > 0);
  const orphans = roots.filter((r) => (parentToChildren.get(r.id) ?? []).length === 0);

  // Largest trees first (by all descendants, not just direct children).
  const trees = withChildren
    .sort((a, b) => a.name.localeCompare(b.name))
    .map((r) => build(r))
    .sort((a, b) => countDescendants(b) - countDescendants(a));

  return { trees, orphans: orphans.sort((a, b) => a.name.localeCompare(b.name)) };
}

/**
 * Keep only the branches that contain a matching node (the path from the
 * root stays visible for context). Returns null if nothing in the tree matches.
 */
export function pruneTree(t: TreeNode, match: (n: TopoNode) => boolean): TreeNode | null {
  const children = t.children.map((c) => pruneTree(c, match)).filter((c): c is TreeNode => c !== null);
  if (children.length > 0 || match(t.node)) return { node: t.node, children };
  return null;
}

export function countDescendants(t: TreeNode): number {
  return t.children.reduce((sum, c) => sum + 1 + countDescendants(c), 0);
}

/** Worst state below a node (not including itself): drives the branch glow. */
export function subtreeHas(t: TreeNode, state: HealthState): boolean {
  return t.children.some((c) => nodeState(c.node) === state || subtreeHas(c, state));
}

export type TopoFilter = 'all' | 'problems';

export function matcher(filter: TopoFilter, search: string): ((n: TopoNode) => boolean) | null {
  const q = search.trim().toLowerCase();
  if (filter === 'all' && !q) return null;
  return (n) => {
    if (filter === 'problems') {
      const s = nodeState(n);
      if (s !== 'down' && s !== 'unknown') return false;
    }
    return !q || n.name.toLowerCase().includes(q) || n.hostname.toLowerCase().includes(q);
  };
}

export function applyFilter(
  trees: TreeNode[],
  orphans: TopoNode[],
  match: ((n: TopoNode) => boolean) | null,
): { trees: TreeNode[]; orphans: TopoNode[] } {
  if (!match) return { trees, orphans };
  return {
    trees: trees.map((t) => pruneTree(t, match)).filter((t): t is TreeNode => t !== null),
    orphans: orphans.filter(match),
  };
}
