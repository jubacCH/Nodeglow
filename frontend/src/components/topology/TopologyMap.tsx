'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Maximize2, ZoomIn, ZoomOut } from 'lucide-react';
import { IconButton } from '@/components/ui/Button';
import { HEALTH_LABEL, glows } from '@/lib/status';
import { nodeState, type TopoNode, type TreeNode } from './model';
import { useTopologyPalette, type TopologyPalette } from './palette';

/* ── Layout engine: x,y for each node ── */

const NODE_W = 200;
const NODE_H = 60;
const GAP_X = 40;
const GAP_Y = 80;
const HEIGHT = 620;

interface LayoutNode {
  id: number;
  x: number;
  y: number;
  node: TopoNode;
  parentId: number | null;
}

function layoutTree(tree: TreeNode, offsetX: number): { nodes: LayoutNode[]; width: number } {
  const result: LayoutNode[] = [];
  const widths = new Map<TreeNode, number>();
  function measure(t: TreeNode): number {
    const cached = widths.get(t);
    if (cached !== undefined) return cached;
    const w = t.children.length === 0 ? NODE_W : t.children.reduce((sum, c) => sum + measure(c) + GAP_X, -GAP_X);
    widths.set(t, w);
    return w;
  }
  function place(t: TreeNode, x: number, y: number, parentId: number | null) {
    const totalW = measure(t);
    result.push({ id: t.node.id, x: x + totalW / 2 - NODE_W / 2, y, node: t.node, parentId });
    let childX = x;
    for (const child of t.children) {
      place(child, childX, y + NODE_H + GAP_Y, t.node.id);
      childX += measure(child) + GAP_X;
    }
  }
  place(tree, offsetX, 0, null);
  return { nodes: result, width: measure(tree) };
}

/* ── Drawing helpers ── */

function hatchPattern(ctx: CanvasRenderingContext2D, p: TopologyPalette): CanvasPattern | string {
  const tile = document.createElement('canvas');
  tile.width = tile.height = 6;
  const t = tile.getContext('2d');
  if (!t) return p.hatchBg;
  t.fillStyle = p.hatchBg;
  t.fillRect(0, 0, 6, 6);
  t.strokeStyle = p.state.unknown;
  t.lineWidth = 1.5;
  t.beginPath();
  t.moveTo(-1, 7); t.lineTo(7, -1);
  t.moveTo(5, 7); t.lineTo(7, 5);
  t.moveTo(-1, 1); t.lineTo(1, -1);
  t.stroke();
  return ctx.createPattern(tile, 'repeat') ?? p.hatchBg;
}

function ellipsize(ctx: CanvasRenderingContext2D, text: string, max: number): string {
  if (ctx.measureText(text).width <= max) return text;
  let s = text;
  while (s.length > 1 && ctx.measureText(`${s}…`).width > max) s = s.slice(0, -1);
  return `${s}…`;
}

/**
 * Pan/zoom map on a <canvas>. Colours come from the tokens (useTopologyPalette)
 * and are re-read on theme change. Glow only on critical (down) nodes and
 * their wire; healthy, maintenance and unknown never glow, not even on hover.
 * Unknown is hatched with a dashed border and dashed wire. Nothing animates,
 * so reduced-motion needs no special case. The tree view is the keyboard and
 * screen-reader equivalent of this map.
 */
export function TopologyMap({ trees, orphans }: { trees: TreeNode[]; orphans: TopoNode[] }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const router = useRouter();
  const palette = useTopologyPalette();
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 40, y: 40 });
  const [dragging, setDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  const [moved, setMoved] = useState(false);
  const [hoveredId, setHoveredId] = useState<number | null>(null);

  const { allNodes, edges, totalW, totalH } = useMemo(() => {
    const allNodes: LayoutNode[] = [];
    const edges: { from: LayoutNode; to: LayoutNode }[] = [];
    let offsetX = 0;
    let maxH = 0;
    for (const tree of trees) {
      const { nodes, width } = layoutTree(tree, offsetX);
      allNodes.push(...nodes);
      offsetX += width + GAP_X * 3;
      const maxY = Math.max(...nodes.map((n) => n.y));
      if (maxY + NODE_H > maxH) maxH = maxY + NODE_H;
    }
    if (orphans.length > 0) {
      const orphanY = trees.length ? maxH + GAP_Y * 1.5 : 0;
      const cols = Math.max(Math.ceil(Math.sqrt(orphans.length * 2)), 4);
      orphans.forEach((o, i) => {
        allNodes.push({
          id: o.id,
          x: (i % cols) * (NODE_W + GAP_X / 2),
          y: orphanY + Math.floor(i / cols) * (NODE_H + GAP_X / 2),
          node: o,
          parentId: null,
        });
      });
      maxH = orphanY + Math.ceil(orphans.length / cols) * (NODE_H + GAP_X / 2);
      offsetX = Math.max(offsetX, cols * (NODE_W + GAP_X / 2));
    }
    const byId = new Map(allNodes.map((n) => [n.id, n]));
    for (const n of allNodes) {
      if (n.parentId != null) {
        const parent = byId.get(n.parentId);
        if (parent) edges.push({ from: parent, to: n });
      }
    }
    return { allNodes, edges, totalW: offsetX || NODE_W, totalH: maxH + NODE_H };
  }, [trees, orphans]);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;
    const p = palette;
    const dark = p.theme === 'dark';

    const dpr = window.devicePixelRatio || 1;
    const rect = container.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = HEIGHT * dpr;
    canvas.style.width = `${rect.width}px`;
    canvas.style.height = `${HEIGHT}px`;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, rect.width, HEIGHT);
    ctx.save();
    ctx.translate(pan.x, pan.y);
    ctx.scale(zoom, zoom);
    const hatch = hatchPattern(ctx, p);

    const setGlow = (on: boolean) => {
      if (on) {
        ctx.shadowColor = p.glow;
        ctx.shadowBlur = dark ? 16 : 10;
        ctx.shadowOffsetY = dark ? 0 : 3; // light mode: tinted shadow below, never light on white
      } else {
        ctx.shadowColor = 'transparent';
        ctx.shadowBlur = 0;
        ctx.shadowOffsetY = 0;
      }
    };

    // Wires: coloured by the child's state.
    for (const { from, to } of edges) {
      const st = nodeState(to.node);
      const fx = from.x + NODE_W / 2;
      const fy = from.y + NODE_H;
      const tx = to.x + NODE_W / 2;
      const ty = to.y;
      const my = fy + (ty - fy) / 2;
      ctx.beginPath();
      ctx.moveTo(fx, fy);
      ctx.bezierCurveTo(fx, my, tx, my, tx, ty);
      ctx.lineWidth = st === 'down' ? 2 : 1.5;
      ctx.strokeStyle = st === 'down' ? p.state.down : st === 'unknown' ? p.state.unknown : p.line;
      ctx.setLineDash(st === 'unknown' ? [4, 4] : []);
      setGlow(st === 'down' && dark); // wires glow only in dark mode
      ctx.stroke();
    }
    ctx.setLineDash([]);
    setGlow(false);

    // Nodes
    for (const ln of allNodes) {
      const st = nodeState(ln.node);
      const hovered = hoveredId === ln.id;
      const { x, y } = ln;

      ctx.beginPath();
      ctx.roundRect(x, y, NODE_W, NODE_H, 9);
      setGlow(glows(st) === 'crit');
      ctx.fillStyle = st === 'down' ? p.downFill : hovered ? p.nodeHover : p.node;
      ctx.fill();
      setGlow(false);
      ctx.lineWidth = 1;
      ctx.strokeStyle = st === 'down' ? p.downBorder : hovered ? p.borderHover : st === 'unknown' ? p.borderHover : p.border;
      ctx.setLineDash(st === 'unknown' ? [4, 3] : []);
      ctx.stroke();
      ctx.setLineDash([]);

      // Status dot: unknown is a hollow ring on hatch, maintenance muted, no glow except down.
      const cx = x + 18;
      const cy = y + NODE_H / 2;
      ctx.beginPath();
      ctx.arc(cx, cy, 5, 0, Math.PI * 2);
      if (st === 'unknown') {
        ctx.fillStyle = hatch;
        ctx.fill();
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = p.state.unknown;
        ctx.stroke();
      } else {
        setGlow(st === 'down');
        ctx.fillStyle = p.state[st];
        ctx.fill();
        setGlow(false);
      }

      // Name
      ctx.textBaseline = 'alphabetic';
      ctx.textAlign = 'left';
      ctx.font = `500 13px ${p.fontSans}`;
      ctx.fillStyle = st === 'unknown' ? p.text2 : p.text;
      const nameMax = st === 'ok' ? NODE_W - 44 : NODE_W - 44 - 64;
      ctx.fillText(ellipsize(ctx, ln.node.name, nameMax), x + 32, y + 25);

      // Hostname
      ctx.font = `400 11px ${p.fontMono}`;
      ctx.fillStyle = p.text3;
      ctx.fillText(ellipsize(ctx, ln.node.hostname, NODE_W - 44), x + 32, y + 43);

      // State word (not for ok: no badge noise on healthy nodes)
      if (st !== 'ok') {
        ctx.font = `600 11px ${p.fontSans}`;
        ctx.textAlign = 'right';
        ctx.fillStyle = p.stateText[st];
        ctx.fillText(HEALTH_LABEL[st], x + NODE_W - 10, y + 25);
        ctx.textAlign = 'left';
      }
    }
    ctx.restore();
  }, [allNodes, edges, pan, zoom, hoveredId, palette]);

  useEffect(() => { draw(); }, [draw]);

  useEffect(() => {
    const obs = new ResizeObserver(() => draw());
    if (containerRef.current) obs.observe(containerRef.current);
    return () => obs.disconnect();
  }, [draw]);

  const hitTest = useCallback((clientX: number, clientY: number): number | null => {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    const mx = (clientX - rect.left - pan.x) / zoom;
    const my = (clientY - rect.top - pan.y) / zoom;
    for (const ln of allNodes) {
      if (mx >= ln.x && mx <= ln.x + NODE_W && my >= ln.y && my <= ln.y + NODE_H) return ln.id;
    }
    return null;
  }, [allNodes, pan, zoom]);

  const onPointerDown = (e: React.PointerEvent) => {
    setDragging(true);
    setMoved(false);
    setDragStart({ x: e.clientX - pan.x, y: e.clientY - pan.y });
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (dragging) {
      setMoved(true);
      setPan({ x: e.clientX - dragStart.x, y: e.clientY - dragStart.y });
    }
    const id = hitTest(e.clientX, e.clientY);
    setHoveredId(id);
    if (canvasRef.current) canvasRef.current.style.cursor = id != null && !dragging ? 'pointer' : dragging ? 'grabbing' : 'grab';
  };
  const onClick = (e: React.MouseEvent) => {
    if (moved) return; // a drag is not a click
    const id = hitTest(e.clientX, e.clientY);
    if (id != null) router.push(`/hosts/${id}`);
  };

  // Wheel zoom needs a non-passive listener to stop the page from scrolling.
  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const delta = e.deltaY > 0 ? 0.9 : 1.1;
      setZoom((z) => Math.max(0.3, Math.min(3, z * delta)));
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, []);

  const fitView = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    const padding = 40;
    const fit = Math.min((rect.width - padding * 2) / totalW, (HEIGHT - padding * 2) / totalH, 1.5);
    // Below ~60 % labels are unreadable: keep them legible and let the user pan.
    const scale = Math.max(fit, 0.6);
    setZoom(scale);
    setPan({ x: scale > fit ? padding : (rect.width - totalW * scale) / 2, y: padding });
  }, [totalW, totalH]);

  useEffect(() => { fitView(); }, [fitView]);

  const downCount = allNodes.filter((n) => nodeState(n.node) === 'down').length;
  const unknownCount = allNodes.filter((n) => nodeState(n.node) === 'unknown').length;

  return (
    <div className="relative" ref={containerRef}>
      <canvas
        ref={canvasRef}
        role="img"
        aria-label={`Topology map with ${allNodes.length} nodes, ${downCount} down, ${unknownCount} without data. Use the tree view to navigate with the keyboard.`}
        className="block w-full touch-none"
        style={{ height: HEIGHT }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={() => setDragging(false)}
        onPointerLeave={() => { setDragging(false); setHoveredId(null); }}
        onClick={onClick}
      />
      <div className="absolute right-3 top-3 flex flex-col gap-1 rounded-ctl border border-border bg-surface p-1">
        <IconButton size="sm" aria-label="Zoom in" title="Zoom in" onClick={() => setZoom((z) => Math.min(3, z * 1.2))}>
          <ZoomIn size={14} aria-hidden="true" />
        </IconButton>
        <IconButton size="sm" aria-label="Zoom out" title="Zoom out" onClick={() => setZoom((z) => Math.max(0.3, z / 1.2))}>
          <ZoomOut size={14} aria-hidden="true" />
        </IconButton>
        <IconButton size="sm" aria-label="Fit to view" title="Fit to view" onClick={fitView}>
          <Maximize2 size={14} aria-hidden="true" />
        </IconButton>
      </div>
      <p className="pointer-events-none absolute bottom-3 left-3 text-meta text-fg-3">Drag to pan · scroll to zoom · click a node to open it</p>
    </div>
  );
}
