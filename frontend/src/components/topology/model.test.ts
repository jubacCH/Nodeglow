import { describe, expect, it } from 'vitest';
import { applyFilter, buildTrees, countDescendants, matcher, nodeState, subtreeHas, type TopoNode } from './model';

const node = (id: number, name: string, status: TopoNode['status'] = 'up', maintenance = false): TopoNode => ({
  id, name, hostname: `${name}.example.test`, status, check_type: 'icmp', source: 'manual', maintenance,
});

const nodes = [
  node(1, 'fw'), node(2, 'core'), node(3, 'srv-a', 'down'), node(4, 'srv-b'),
  node(5, 'probe', 'unknown'), node(6, 'nas', 'unknown'), node(7, 'lonely'), node(8, 'printer', 'up', true),
];
const edges = [
  { source: 1, target: 2 }, { source: 2, target: 3 }, { source: 2, target: 4 }, { source: 5, target: 6 },
];

describe('topology model', () => {
  it('maps status honestly: maintenance first, unknown never ok', () => {
    expect(nodeState(node(1, 'a', 'up'))).toBe('ok');
    expect(nodeState(node(1, 'a', 'down', true))).toBe('maint');
    expect(nodeState(node(1, 'a', 'unknown'))).toBe('unknown');
  });

  it('builds trees (biggest first) and orphans', () => {
    const { trees, orphans } = buildTrees(nodes, edges);
    expect(trees.map((t) => t.node.name)).toEqual(['fw', 'probe']);
    expect(countDescendants(trees[0])).toBe(3);
    expect(orphans.map((o) => o.name)).toEqual(['lonely', 'printer']);
    expect(subtreeHas(trees[0], 'down')).toBe(true);
    expect(subtreeHas(trees[1], 'down')).toBe(false);
  });

  it('survives parent cycles', () => {
    const { trees } = buildTrees([node(1, 'a'), node(2, 'b'), node(3, 'root')], [
      { source: 3, target: 1 }, { source: 1, target: 2 }, { source: 2, target: 1 },
    ]);
    expect(countDescendants(trees[0])).toBe(2);
  });

  it('filters to problem branches and keeps the path', () => {
    const { trees, orphans } = buildTrees(nodes, edges);
    const f = applyFilter(trees, orphans, matcher('problems', ''));
    expect(f.trees.map((t) => t.node.name)).toEqual(['fw', 'probe']);
    expect(f.trees[0].children[0].children.map((c) => c.node.name)).toEqual(['srv-a']);
    expect(f.orphans).toEqual([]);
    const s = applyFilter(trees, orphans, matcher('all', 'LONE'));
    expect(s.trees).toEqual([]);
    expect(s.orphans.map((o) => o.name)).toEqual(['lonely']);
    expect(matcher('all', '  ')).toBeNull();
  });
});
