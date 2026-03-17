'use client';

import { useEffect, useRef, useMemo, useState, useCallback } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { OrbitControls, Html } from '@react-three/drei';
import { useRouter } from 'next/navigation';
import * as THREE from 'three';
import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Skeleton } from '@/components/ui/Skeleton';
import { Badge } from '@/components/ui/Badge';
import { useTopology } from '@/hooks/queries/useTopology';
import { Network, Maximize2, Minimize2 } from 'lucide-react';

/* ── Types ── */

interface TopoNode {
  id: number;
  name: string;
  hostname: string;
  status: 'up' | 'down';
  check_type: string;
  source: string;
  maintenance: boolean;
}

interface TopoEdge {
  source: number;
  target: number;
}

/* ── Layout: force-directed spring simulation ── */

function computeLayout(
  nodes: TopoNode[],
  edges: TopoEdge[],
  iterations = 120,
): Map<number, [number, number, number]> {
  const positions = new Map<number, [number, number, number]>();

  // Identify root nodes (no parent)
  const childIds = new Set(edges.map((e) => e.target));
  const rootIds = nodes.filter((n) => !childIds.has(n.id)).map((n) => n.id);

  // Init positions — roots near center, others spread out
  nodes.forEach((n, i) => {
    const isRoot = rootIds.includes(n.id);
    if (isRoot) {
      const angle = (i / Math.max(rootIds.length, 1)) * Math.PI * 2;
      positions.set(n.id, [Math.cos(angle) * 1.0, 0.5, Math.sin(angle) * 1.0]);
    } else {
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      const r = 1.5 + Math.random() * 2;
      positions.set(n.id, [
        r * Math.sin(phi) * Math.cos(theta),
        r * Math.cos(phi),
        r * Math.sin(phi) * Math.sin(theta),
      ]);
    }
  });

  // Simple spring-force simulation
  const edgeMap = new Map<number, number[]>();
  for (const e of edges) {
    if (!edgeMap.has(e.source)) edgeMap.set(e.source, []);
    if (!edgeMap.has(e.target)) edgeMap.set(e.target, []);
    edgeMap.get(e.source)!.push(e.target);
    edgeMap.get(e.target)!.push(e.source);
  }

  const ids = nodes.map((n) => n.id);

  for (let iter = 0; iter < iterations; iter++) {
    const temp = 0.3 * (1 - iter / iterations);

    for (let i = 0; i < ids.length; i++) {
      const a = ids[i];
      const pa = positions.get(a)!;
      let fx = 0, fy = 0, fz = 0;

      // Repulsion from all other nodes
      for (let j = 0; j < ids.length; j++) {
        if (i === j) continue;
        const b = ids[j];
        const pb = positions.get(b)!;
        const dx = pa[0] - pb[0], dy = pa[1] - pb[1], dz = pa[2] - pb[2];
        const dist = Math.max(Math.sqrt(dx * dx + dy * dy + dz * dz), 0.1);
        const force = 2.0 / (dist * dist);
        fx += (dx / dist) * force;
        fy += (dy / dist) * force;
        fz += (dz / dist) * force;
      }

      // Spring attraction along edges
      const neighbors = edgeMap.get(a) ?? [];
      for (const b of neighbors) {
        const pb = positions.get(b)!;
        const dx = pb[0] - pa[0], dy = pb[1] - pa[1], dz = pb[2] - pa[2];
        const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
        const ideal = 1.8;
        const force = (dist - ideal) * 0.15;
        fx += (dx / Math.max(dist, 0.1)) * force;
        fy += (dy / Math.max(dist, 0.1)) * force;
        fz += (dz / Math.max(dist, 0.1)) * force;
      }

      // Gentle gravity toward center
      fx -= pa[0] * 0.02;
      fy -= pa[1] * 0.02;
      fz -= pa[2] * 0.02;

      positions.set(a, [
        pa[0] + fx * temp,
        pa[1] + fy * temp,
        pa[2] + fz * temp,
      ]);
    }
  }

  return positions;
}

/* ── Colors ── */

function nodeColor(n: TopoNode): string {
  if (n.maintenance) return '#FBBF24';
  if (n.status === 'down') return '#F87171';
  return '#34D399';
}

/* ── Animated edge line with glow ── */

function EdgeLine({
  from,
  to,
  color,
}: {
  from: [number, number, number];
  to: [number, number, number];
  color: string;
}) {
  const lineObj = useMemo(() => {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute(
      'position',
      new THREE.Float32BufferAttribute([...from, ...to], 3),
    );
    const material = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.35 });
    return new THREE.Line(geometry, material);
  }, [from, to, color]);

  return <primitive object={lineObj} />;
}

/* ── Animated pulse ring for offline nodes ── */

function PulseRing({ color, radius }: { color: string; radius: number }) {
  const ref = useRef<THREE.Mesh>(null);
  useFrame(({ clock }) => {
    if (!ref.current) return;
    const t = (clock.getElapsedTime() * 1.5) % 1;
    ref.current.scale.setScalar(1 + t * 1.5);
    (ref.current.material as THREE.MeshBasicMaterial).opacity = 0.3 * (1 - t);
  });
  return (
    <mesh ref={ref} rotation-x={Math.PI / 2}>
      <ringGeometry args={[radius * 1.2, radius * 1.5, 32]} />
      <meshBasicMaterial color={color} transparent side={THREE.DoubleSide} depthWrite={false} />
    </mesh>
  );
}

/* ── Data flow particle along edge ── */

function FlowParticle({
  from,
  to,
  speed = 1,
}: {
  from: [number, number, number];
  to: [number, number, number];
  speed?: number;
}) {
  const ref = useRef<THREE.Mesh>(null);
  const offset = useMemo(() => Math.random(), []);

  useFrame(({ clock }) => {
    if (!ref.current) return;
    const t = ((clock.getElapsedTime() * speed * 0.3 + offset) % 1);
    ref.current.position.set(
      from[0] + (to[0] - from[0]) * t,
      from[1] + (to[1] - from[1]) * t,
      from[2] + (to[2] - from[2]) * t,
    );
  });

  return (
    <mesh ref={ref}>
      <sphereGeometry args={[0.03, 8, 8]} />
      <meshBasicMaterial color="#38BDF8" transparent opacity={0.7} />
    </mesh>
  );
}

/* ── Background star particles ── */

function Stars({ count = 300 }: { count?: number }) {
  const ref = useRef<THREE.Points>(null);
  const geometry = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    const arr = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      const r = 8 + Math.random() * 4;
      arr[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      arr[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      arr[i * 3 + 2] = r * Math.cos(phi);
    }
    geo.setAttribute('position', new THREE.Float32BufferAttribute(arr, 3));
    return geo;
  }, [count]);

  useFrame((_state, delta) => {
    if (ref.current) {
      ref.current.rotation.y += delta * 0.015;
    }
  });

  return (
    <points ref={ref} geometry={geometry}>
      <pointsMaterial size={0.02} color="#94a3b8" transparent opacity={0.4} sizeAttenuation depthWrite={false} />
    </points>
  );
}

/* ── Single host node ── */

function HostNode({
  node,
  position,
  isParent,
}: {
  node: TopoNode;
  position: [number, number, number];
  isParent: boolean;
}) {
  const meshRef = useRef<THREE.Mesh>(null);
  const [hovered, setHovered] = useState(false);
  const router = useRouter();
  const color = nodeColor(node);
  const radius = isParent ? 0.22 : 0.14;
  const isOffline = node.status === 'down' && !node.maintenance;

  useFrame(({ clock }) => {
    if (!meshRef.current) return;
    if (isOffline) {
      const s = 1 + Math.sin(clock.getElapsedTime() * 3) * 0.12;
      meshRef.current.scale.setScalar(s);
    }
    // Gentle float
    meshRef.current.position.y = position[1] + Math.sin(clock.getElapsedTime() * 0.5 + position[0]) * 0.04;
  });

  const handleClick = useCallback(() => {
    router.push(`/hosts/${node.id}`);
  }, [router, node.id]);

  return (
    <group position={position}>
      {/* Core sphere */}
      <mesh
        ref={meshRef}
        onClick={handleClick}
        onPointerOver={() => { setHovered(true); document.body.style.cursor = 'pointer'; }}
        onPointerOut={() => { setHovered(false); document.body.style.cursor = 'default'; }}
      >
        <sphereGeometry args={[radius, 32, 32]} />
        <meshStandardMaterial
          color={color}
          emissive={color}
          emissiveIntensity={hovered ? 2.0 : 0.8}
          roughness={0.3}
          metalness={0.6}
          transparent
          opacity={0.95}
        />
      </mesh>

      {/* Outer glow */}
      <mesh>
        <sphereGeometry args={[radius * 2.2, 16, 16]} />
        <meshBasicMaterial
          color={color}
          transparent
          opacity={hovered ? 0.15 : 0.06}
          depthWrite={false}
        />
      </mesh>

      {/* Pulse ring for offline */}
      {isOffline && <PulseRing color={color} radius={radius} />}

      {/* Label */}
      <Html
        distanceFactor={8}
        position={[0, radius + 0.2, 0]}
        style={{ pointerEvents: 'none', userSelect: 'none' }}
        center
      >
        <div className={`text-[10px] font-medium whitespace-nowrap transition-opacity ${hovered ? 'opacity-100' : 'opacity-70'}`}
          style={{ color, textShadow: '0 0 8px rgba(0,0,0,0.8)' }}
        >
          {node.name}
        </div>
      </Html>

      {/* Hover tooltip */}
      {hovered && (
        <Html distanceFactor={6} position={[0, -radius - 0.3, 0]} center style={{ pointerEvents: 'none' }}>
          <div className="rounded-lg bg-slate-900/95 border border-white/10 px-4 py-2.5 text-xs text-white whitespace-nowrap backdrop-blur-md shadow-2xl">
            <p className="font-semibold text-sm mb-1">{node.name}</p>
            <p className="text-slate-400">{node.hostname}</p>
            <div className="flex gap-3 mt-1.5 text-[10px]">
              <span className="text-slate-500">Type: <span className="text-slate-300">{node.check_type}</span></span>
              <span className="text-slate-500">Source: <span className="text-slate-300">{node.source}</span></span>
            </div>
            <div className="mt-1.5">
              {node.maintenance ? (
                <span className="text-amber-400 font-medium">Maintenance</span>
              ) : node.status === 'up' ? (
                <span className="text-emerald-400 font-medium">Online</span>
              ) : (
                <span className="text-red-400 font-medium">Offline</span>
              )}
            </div>
          </div>
        </Html>
      )}
    </group>
  );
}

/* ── 3D Scene ── */

function TopologyScene({
  nodes,
  edges,
}: {
  nodes: TopoNode[];
  edges: TopoEdge[];
}) {
  const positions = useMemo(() => computeLayout(nodes, edges), [nodes, edges]);
  const parentIds = useMemo(() => new Set(edges.map((e) => e.source)), [edges]);

  return (
    <>
      <ambientLight intensity={0.25} />
      <pointLight position={[6, 6, 6]} intensity={0.6} color="#ffffff" />
      <pointLight position={[-4, -2, -6]} intensity={0.3} color="#38BDF8" />
      <pointLight position={[0, -5, 3]} intensity={0.2} color="#8B5CF6" />

      <Stars />

      {/* Edges */}
      {edges.map((e) => {
        const from = positions.get(e.source);
        const to = positions.get(e.target);
        if (!from || !to) return null;
        const sourceNode = nodes.find((n) => n.id === e.source);
        const targetNode = nodes.find((n) => n.id === e.target);
        const bothOnline = sourceNode?.status === 'up' && targetNode?.status === 'up';
        return (
          <group key={`${e.source}-${e.target}`}>
            <EdgeLine from={from} to={to} color={bothOnline ? '#38BDF8' : '#64748B'} />
            {bothOnline && <FlowParticle from={from} to={to} />}
          </group>
        );
      })}

      {/* Nodes */}
      {nodes.map((n) => {
        const pos = positions.get(n.id);
        if (!pos) return null;
        return (
          <HostNode
            key={n.id}
            node={n}
            position={pos}
            isParent={parentIds.has(n.id)}
          />
        );
      })}

      <OrbitControls
        enablePan
        minDistance={2}
        maxDistance={15}
        autoRotate
        autoRotateSpeed={0.2}
        dampingFactor={0.05}
        enableDamping
      />
    </>
  );
}

/* ── Page ── */

export default function TopologyPage() {
  useEffect(() => { document.title = 'Topology | Nodeglow'; }, []);
  const { data, isLoading } = useTopology();
  const [fullscreen, setFullscreen] = useState(false);

  const onlineCount = data?.nodes.filter((n) => n.status === 'up' && !n.maintenance).length ?? 0;
  const offlineCount = data?.nodes.filter((n) => n.status === 'down' && !n.maintenance).length ?? 0;
  const maintCount = data?.nodes.filter((n) => n.maintenance).length ?? 0;
  const edgeCount = data?.edges.length ?? 0;

  return (
    <div>
      <PageHeader title="Network Topology" description="Interactive 3D map of your infrastructure" />

      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mb-6">
        {[
          { label: 'Nodes', value: data?.nodes.length ?? 0, color: 'var(--ng-text-primary)' },
          { label: 'Online', value: onlineCount, color: '#34d399' },
          { label: 'Offline', value: offlineCount, color: '#f87171' },
          { label: 'Maintenance', value: maintCount, color: '#fbbf24' },
          { label: 'Connections', value: edgeCount, color: '#38bdf8' },
        ].map((s) => (
          <GlassCard key={s.label} className="p-3 text-center">
            <p className="text-xl font-bold font-mono" style={{ color: s.color }}>{s.value}</p>
            <p className="text-[10px] text-slate-500 uppercase tracking-wider">{s.label}</p>
          </GlassCard>
        ))}
      </div>

      {/* 3D Graph */}
      <GlassCard className={`relative overflow-hidden ${fullscreen ? 'fixed inset-0 z-50 rounded-none' : ''}`}>
        <div className="absolute top-3 left-4 z-10 flex items-center gap-2">
          <Network size={14} className="text-sky-400" />
          <span className="text-xs font-medium" style={{ color: 'var(--ng-text-primary)' }}>3D Topology</span>
        </div>
        <div className="absolute top-3 right-4 z-10 flex items-center gap-2">
          <span className="text-[10px] text-slate-500">Click node to open · Drag to rotate · Scroll to zoom</span>
          <button
            onClick={() => setFullscreen(!fullscreen)}
            className="p-1.5 rounded-md hover:bg-white/[0.1] transition-colors text-slate-400"
          >
            {fullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
          </button>
        </div>
        {isLoading ? (
          <Skeleton className={`w-full ${fullscreen ? 'h-screen' : 'h-[600px]'}`} />
        ) : !data || data.nodes.length === 0 ? (
          <div className="flex flex-col items-center justify-center text-slate-500" style={{ height: fullscreen ? '100vh' : 600 }}>
            <Network size={48} className="mb-3 opacity-20" />
            <p className="text-sm">No topology data</p>
            <p className="text-xs mt-1 text-slate-600">Add hosts and integrations to build the graph</p>
          </div>
        ) : (
          <div style={{ height: fullscreen ? '100vh' : 600 }}>
            <Canvas
              camera={{ position: [0, 3, 8], fov: 50 }}
              style={{ background: 'transparent' }}
              gl={{ alpha: true, antialias: true }}
              dpr={[1, 2]}
            >
              <TopologyScene nodes={data.nodes} edges={data.edges} />
            </Canvas>
          </div>
        )}

        {/* Legend */}
        <div className="absolute bottom-3 left-4 z-10 flex items-center gap-4">
          {[
            { color: '#34D399', label: 'Online' },
            { color: '#F87171', label: 'Offline' },
            { color: '#FBBF24', label: 'Maintenance' },
          ].map((l) => (
            <div key={l.label} className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-full" style={{ background: l.color, boxShadow: `0 0 6px ${l.color}` }} />
              <span className="text-[10px] text-slate-400">{l.label}</span>
            </div>
          ))}
          <div className="flex items-center gap-1.5">
            <span className="w-4 h-[1px]" style={{ background: '#38BDF8' }} />
            <span className="text-[10px] text-slate-400">Connection</span>
          </div>
        </div>
      </GlassCard>

      {/* Node list */}
      {data && data.nodes.length > 0 && (
        <GlassCard className="p-4 mt-4">
          <h3 className="text-sm font-medium mb-3" style={{ color: 'var(--ng-text-primary)' }}>All Nodes</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {data.nodes.map((n) => (
              <a
                key={n.id}
                href={`/hosts/${n.id}`}
                className="flex items-center gap-3 px-3 py-2.5 rounded-lg border border-white/[0.06] hover:bg-white/[0.04] hover:border-white/[0.12] transition-all group"
              >
                <div className="relative">
                  <span
                    className="block w-3 h-3 rounded-full"
                    style={{ background: nodeColor(n), boxShadow: `0 0 8px ${nodeColor(n)}40` }}
                  />
                  {n.status === 'down' && !n.maintenance && (
                    <span className="absolute inset-0 w-3 h-3 rounded-full animate-ping" style={{ background: nodeColor(n), opacity: 0.4 }} />
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium truncate group-hover:text-sky-400 transition-colors" style={{ color: 'var(--ng-text-primary)' }}>
                    {n.name}
                  </p>
                  <p className="text-[11px] text-slate-500 truncate">{n.hostname}</p>
                </div>
                {n.maintenance && <Badge variant="severity" severity="warning">Maint</Badge>}
                <Badge>{n.source}</Badge>
              </a>
            ))}
          </div>
        </GlassCard>
      )}
    </div>
  );
}
