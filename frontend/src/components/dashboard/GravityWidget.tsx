'use client';

import { useState, useEffect, useLayoutEffect, useMemo, useRef, useCallback } from 'react';
import { Canvas, useFrame, useThree, type ThreeEvent } from '@react-three/fiber';
import { OrbitControls, Html, useTexture } from '@react-three/drei';
import { useRouter } from 'next/navigation';
import * as THREE from 'three';
import { GlassCard } from '@/components/ui/GlassCard';
import { StatusDot } from '@/components/ui/StatusDot';
import Link from 'next/link';
import type { HostStat } from '@/hooks/queries/useDashboard';
import { usePrefersReducedMotion } from '@/hooks/usePrefersReducedMotion';

/* ── Health scoring ── */

function hostHealth(h: HostStat): number {
  if (h.host.maintenance) return 0.4;
  if (h.online === false) return 1.0;
  if (h.online === null) return 0.7;
  let score = 0;
  if (h.latency != null) {
    if (h.latency > 200) score += 0.3;
    else if (h.latency > 100) score += 0.15;
    else if (h.latency > 50) score += 0.05;
  }
  const uptime = h.uptime_stats?.h24;
  if (uptime != null && uptime < 100) {
    score += (1 - uptime / 100) * 0.4;
  }
  return Math.min(score, 1);
}

function hostColor(h: HostStat): string {
  if (h.host.maintenance) return '#FBBF24';
  if (h.online === false) return '#EF4444';
  if (h.online === null) return '#64748B';
  if (h.host.port_error) return '#F97316';
  const health = hostHealth(h);
  if (health >= 0.5) return '#EF4444';
  if (health >= 0.2) return '#FBBF24';
  return '#10B981';
}

/* ── Orbit radius ── */

function orbitRadius(h: HostStat, index: number): number {
  if (h.online === false) {
    return 3.5 + (index % 5) * 0.15;
  }
  if (h.host.maintenance) {
    return 1.8 + (index % 4) * 0.15;
  }
  const health = hostHealth(h);
  const base = 1.4 + health * 2.5;
  const spread = ((index * 7) % 13) / 13 * 0.8 + ((index * 3) % 7) / 7 * 0.4;
  return base + spread;
}

/* ── Deep space background ── */

function SpaceBackground() {
  const { scene } = useThree();

  useMemo(() => {
    const canvas = document.createElement('canvas');
    canvas.width = 2048;
    canvas.height = 2048;
    const ctx = canvas.getContext('2d')!;

    // Deep space gradient
    const grad = ctx.createRadialGradient(1024, 1024, 0, 1024, 1024, 1200);
    grad.addColorStop(0, '#0a0e1a');
    grad.addColorStop(0.3, '#060a14');
    grad.addColorStop(0.6, '#030510');
    grad.addColorStop(1, '#010208');
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, 2048, 2048);

    // Subtle nebula patches
    for (let n = 0; n < 4; n++) {
      const nx = 400 + Math.random() * 1200;
      const ny = 400 + Math.random() * 1200;
      const nr = 200 + Math.random() * 300;
      const colors = ['#1e3a5f', '#2d1b4e', '#1a3045', '#261840'];
      const ng = ctx.createRadialGradient(nx, ny, 0, nx, ny, nr);
      ng.addColorStop(0, colors[n] + '10');
      ng.addColorStop(0.5, colors[n] + '04');
      ng.addColorStop(1, 'transparent');
      ctx.fillStyle = ng;
      ctx.fillRect(0, 0, 2048, 2048);
    }

    // Stars — subtle, no bright glows
    for (let i = 0; i < 600; i++) {
      const sx = Math.random() * 2048;
      const sy = Math.random() * 2048;
      const brightness = Math.random();
      const size = brightness > 0.9 ? 1.2 : brightness > 0.7 ? 0.8 : 0.5;

      const r = 180 + Math.random() * 75;
      const g = 190 + Math.random() * 65;
      const b = 220 + Math.random() * 35;
      ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${0.2 + brightness * 0.5})`;
      ctx.beginPath();
      ctx.arc(sx, sy, size, 0, Math.PI * 2);
      ctx.fill();
    }

    const texture = new THREE.CanvasTexture(canvas);
    texture.mapping = THREE.EquirectangularReflectionMapping;
    scene.background = texture;

    return () => {
      scene.background = null;
      texture.dispose();
    };
  }, [scene]);

  return null;
}

/* ── Twinkling star particles (foreground depth) ── */

function Stars({ count = 150, animate }: { count?: number; animate: boolean }) {
  const ref = useRef<THREE.Points>(null);
  const { geometry } = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    const positions = new Float32Array(count * 3);
    const starSizes = new Float32Array(count);
    const colors = new Float32Array(count * 3);

    for (let i = 0; i < count; i++) {
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      const r = 8 + Math.random() * 6;
      positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      positions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      positions[i * 3 + 2] = r * Math.cos(phi);

      // Varying sizes
      starSizes[i] = 0.015 + Math.random() * 0.03;

      // Blue-white color range
      const warmth = Math.random();
      colors[i * 3] = 0.7 + warmth * 0.3;
      colors[i * 3 + 1] = 0.75 + warmth * 0.2;
      colors[i * 3 + 2] = 0.85 + warmth * 0.15;
    }

    geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    return { geometry: geo, sizes: starSizes };
  }, [count]);

  useFrame((_state, delta) => {
    if (animate && ref.current) ref.current.rotation.y += delta * 0.0015;
  });

  return (
    <points ref={ref} geometry={geometry}>
      <pointsMaterial
        size={0.015}
        vertexColors
        transparent
        opacity={0.35}
        sizeAttenuation
        depthWrite={false}
      />
    </points>
  );
}

/* ── Earth ── */

function Earth({ animate }: { animate: boolean }) {
  const groupRef = useRef<THREE.Group>(null);
  const [dayMap, bumpMap] = useTexture([
    '/textures/earth-day.jpg',
    '/textures/earth-topology.png',
  ]);

  useFrame((_state, delta) => {
    if (animate && groupRef.current) groupRef.current.rotation.y += delta * 0.03;
  });

  return (
    <group ref={groupRef}>
      <mesh>
        <sphereGeometry args={[0.7, 64, 64]} />
        <meshStandardMaterial
          map={dayMap}
          bumpMap={bumpMap}
          bumpScale={0.03}
          roughness={0.7}
          metalness={0.05}
        />
      </mesh>
      {/* Atmosphere layers */}
      <mesh>
        <sphereGeometry args={[0.73, 48, 48]} />
        <meshBasicMaterial color="#60A5FA" transparent opacity={0.12} side={THREE.BackSide} />
      </mesh>
      <mesh>
        <sphereGeometry args={[0.82, 32, 32]} />
        <meshBasicMaterial color="#38BDF8" transparent opacity={0.07} side={THREE.BackSide} />
      </mesh>
      <mesh>
        <sphereGeometry args={[0.95, 32, 32]} />
        <meshBasicMaterial color="#93C5FD" transparent opacity={0.02} side={THREE.BackSide} />
      </mesh>
      {/* 4th atmosphere — outermost soft bloom */}
      <mesh>
        <sphereGeometry args={[1.05, 24, 24]} />
        <meshBasicMaterial color="#60A5FA" transparent opacity={0.01} side={THREE.BackSide} />
      </mesh>
    </group>
  );
}

/* ── Host nodes (instanced) ── */

interface HostOrbit {
  host: HostStat;
  radius: number;
  angle: number;
  inclination: number;
  speed: number;
  color: THREE.Color;
  isOffline: boolean;
  size: number;
}

const WHITE = new THREE.Color('#ffffff');
// Instances move every frame, so the auto-computed bounding sphere would go
// stale and break raycasting. Every orbit fits comfortably inside this one.
const ORBIT_BOUNDS = new THREE.Sphere(new THREE.Vector3(0, 0, 0), 8);

/**
 * All host nodes in three InstancedMeshes (core, inner glow, outer bloom)
 * driven by a single useFrame — instead of three meshes plus one useFrame
 * callback per host. Hover/click use the raycast instanceId.
 */
function HostNodes({ orbits, animate }: { orbits: HostOrbit[]; animate: boolean }) {
  const coreRef = useRef<THREE.InstancedMesh>(null);
  const innerRef = useRef<THREE.InstancedMesh>(null);
  const outerRef = useRef<THREE.InstancedMesh>(null);
  const tipRef = useRef<THREE.Group>(null);
  const [hoveredId, setHoveredId] = useState<number | null>(null);
  const router = useRouter();
  const invalidate = useThree((s) => s.invalidate);
  const dummy = useMemo(() => new THREE.Object3D(), []);
  const tmpColor = useMemo(() => new THREE.Color(), []);
  const n = orbits.length;
  const hoveredIdx = hoveredId == null ? -1 : orbits.findIndex((o) => o.host.host.id === hoveredId);
  const hovered = hoveredIdx >= 0 ? orbits[hoveredIdx] : null;

  // Per-instance colours. Glow layers use additive blending, so a colour
  // scaled by the old per-mesh opacity reproduces the old look on a dark sky.
  useLayoutEffect(() => {
    const core = coreRef.current;
    const inner = innerRef.current;
    const outer = outerRef.current;
    if (!core || !inner || !outer) return;
    orbits.forEach((o, i) => {
      const isHovered = i === hoveredIdx;
      tmpColor.copy(o.color).lerp(WHITE, isHovered ? 0.6 : 0.3);
      core.setColorAt(i, tmpColor);
      tmpColor.copy(o.color).multiplyScalar(isHovered ? 0.3 : o.isOffline ? 0.12 : 0.06);
      inner.setColorAt(i, tmpColor);
      tmpColor.copy(o.color).multiplyScalar(isHovered ? 0.1 : 0.02);
      outer.setColorAt(i, tmpColor);
    });
    for (const m of [core, inner, outer]) {
      if (m.instanceColor) m.instanceColor.needsUpdate = true;
      m.boundingSphere = ORBIT_BOUNDS;
    }
    // frameloop="demand" (reduced motion): draw the new state once.
    invalidate();
  }, [orbits, hoveredIdx, tmpColor, invalidate]);

  useFrame(({ clock }) => {
    const core = coreRef.current;
    const inner = innerRef.current;
    const outer = outerRef.current;
    if (!core || !inner || !outer) return;
    const elapsed = animate ? clock.getElapsedTime() : 0;
    const pulse = 1 + Math.sin(elapsed * 2.5) * 0.25;
    for (let i = 0; i < n; i++) {
      const o = orbits[i];
      const t = o.angle + elapsed * o.speed;
      dummy.position.set(
        Math.cos(t) * o.radius,
        Math.sin(t + o.inclination) * o.radius * 0.03,
        Math.sin(t) * o.radius,
      );
      dummy.scale.setScalar(o.size * (o.isOffline && animate ? pulse : 1));
      dummy.updateMatrix();
      core.setMatrixAt(i, dummy.matrix);
      dummy.scale.setScalar(o.size * 2);
      dummy.updateMatrix();
      inner.setMatrixAt(i, dummy.matrix);
      dummy.scale.setScalar(o.size * 3);
      dummy.updateMatrix();
      outer.setMatrixAt(i, dummy.matrix);
      if (i === hoveredIdx && tipRef.current) tipRef.current.position.copy(dummy.position);
    }
    core.instanceMatrix.needsUpdate = true;
    inner.instanceMatrix.needsUpdate = true;
    outer.instanceMatrix.needsUpdate = true;
  });

  const onOver = useCallback((e: ThreeEvent<PointerEvent>) => {
    e.stopPropagation();
    if (e.instanceId == null) return;
    setHoveredId(orbits[e.instanceId]?.host.host.id ?? null);
    document.body.style.cursor = 'pointer';
  }, [orbits]);

  const onOut = useCallback(() => {
    setHoveredId(null);
    document.body.style.cursor = 'auto';
  }, []);

  const onClick = useCallback((e: ThreeEvent<MouseEvent>) => {
    e.stopPropagation();
    if (e.instanceId == null) return;
    const id = orbits[e.instanceId]?.host.host.id;
    if (id != null) router.push(`/hosts/${id}`);
  }, [orbits, router]);

  useEffect(() => () => { document.body.style.cursor = 'auto'; }, []);

  if (n === 0) return null;

  const healthPct = hovered ? Math.round((1 - hostHealth(hovered.host)) * 100) : 0;

  return (
    <>
      {/* key={n}: an InstancedMesh's capacity is fixed at construction */}
      <instancedMesh
        key={`core-${n}`}
        ref={coreRef}
        args={[undefined, undefined, n]}
        frustumCulled={false}
        onPointerOver={onOver}
        onPointerOut={onOut}
        onClick={onClick}
      >
        <sphereGeometry args={[1, 20, 20]} />
        <meshBasicMaterial toneMapped={false} />
      </instancedMesh>
      <instancedMesh key={`inner-${n}`} ref={innerRef} args={[undefined, undefined, n]} frustumCulled={false} raycast={() => null}>
        <sphereGeometry args={[1, 16, 16]} />
        <meshBasicMaterial transparent depthWrite={false} blending={THREE.AdditiveBlending} />
      </instancedMesh>
      <instancedMesh key={`outer-${n}`} ref={outerRef} args={[undefined, undefined, n]} frustumCulled={false} raycast={() => null}>
        <sphereGeometry args={[1, 12, 12]} />
        <meshBasicMaterial transparent depthWrite={false} blending={THREE.AdditiveBlending} />
      </instancedMesh>

      <group ref={tipRef}>
        {hovered && (
          <Html distanceFactor={8} style={{ pointerEvents: 'none' }}>
            <div className="rounded-xl px-3 py-2.5 text-xs text-slate-100 whitespace-nowrap shadow-xl" style={{ background: 'var(--ng-card-bg)', border: '1.5px solid var(--ng-card-border)' }}>
              <p className="font-medium text-slate-200">{hovered.host.host.name}</p>
              <p className="text-[10px] text-slate-500 font-mono">{hovered.host.host.hostname}</p>
              <div className="flex items-center gap-2 mt-1">
                <span className={`text-[10px] ${hovered.isOffline ? 'text-red-400' : hovered.host.host.maintenance ? 'text-amber-400' : hovered.host.online === null ? 'text-slate-400' : 'text-emerald-400'}`}>
                  {hovered.host.online === null ? 'Not observed' : hovered.host.online ? 'Online' : 'Offline'}
                </span>
                {hovered.host.latency != null && (
                  <span className="text-[10px] font-mono text-slate-400">{hovered.host.latency.toFixed(0)}ms</span>
                )}
                {hovered.host.uptime_stats?.h24 != null && (
                  <span className="text-[10px] font-mono text-slate-400">{hovered.host.uptime_stats.h24.toFixed(1)}%</span>
                )}
                <span className={`text-[10px] font-mono ${healthPct >= 80 ? 'text-emerald-400' : healthPct >= 50 ? 'text-amber-400' : 'text-red-400'}`}>
                  Health {healthPct}%
                </span>
              </div>
            </div>
          </Html>
        )}
      </group>
    </>
  );
}

/* ── Scene ── */

function Scene({ hosts, animate }: { hosts: HostStat[]; animate: boolean }) {
  const hostOrbits = useMemo<HostOrbit[]>(() => {
    const sorted = [...hosts].sort((a, b) => hostHealth(a) - hostHealth(b));
    const golden = Math.PI * (3 - Math.sqrt(5));

    return sorted.map((h, i) => {
      const r = orbitRadius(h, i);
      const a = i * golden;
      const inclination = ((i * 2.39996 + i * 0.7) % (Math.PI * 2));
      const speed = 0.04 + (1 / (r * 0.6)) * 0.06;
      const isOffline = h.online === false && !h.host.maintenance;
      return {
        host: h, radius: r, angle: a, inclination, speed,
        color: new THREE.Color(hostColor(h)),
        isOffline,
        size: isOffline ? 0.12 : 0.10,
      };
    });
  }, [hosts]);

  return (
    <>
      <SpaceBackground />

      {/* Lighting */}
      <ambientLight intensity={0.25} />
      <directionalLight position={[5, 3, 2]} intensity={1.6} color="#FFF5E6" />
      <pointLight position={[-4, -2, -4]} intensity={0.3} color="#60A5FA" />
      <hemisphereLight args={['#1a2a4a', '#000510', 0.15]} />

      <Stars animate={animate} />
      <Earth animate={animate} />

      <HostNodes orbits={hostOrbits} animate={animate} />

      <OrbitControls
        enablePan={false}
        minDistance={4}
        maxDistance={14}
        autoRotate={animate}
        autoRotateSpeed={0.06}
        enableDamping
        dampingFactor={0.05}
        maxPolarAngle={Math.PI * 0.75}
        minPolarAngle={Math.PI * 0.2}
      />
    </>
  );
}

/* ── Mobile fallback ── */

function MobileGrid({ hosts }: { hosts: HostStat[] }) {
  return (
    <div className="grid grid-cols-4 gap-2 p-4">
      {hosts.map((h) => {
        const status = h.host.maintenance
          ? 'maintenance' as const
          : h.online === false
            ? 'offline' as const
            : h.online === true
              ? 'online' as const
              : 'unknown' as const;
        return (
          <Link
            key={h.host.id}
            prefetch={false} href={`/hosts/${h.host.id}`}
            className="flex flex-col items-center gap-1 p-2 rounded-md hover:bg-white/5 transition-colors"
          >
            <StatusDot status={status} pulse={status === 'offline'} />
            <span className="text-[10px] text-slate-400 truncate max-w-full">
              {h.host.name}
            </span>
          </Link>
        );
      })}
    </div>
  );
}

/* ── Large-fleet fallback ── */

/** Above this many hosts the 3D scene turns into noise; show a grid instead. */
export const GRAVITY_3D_MAX_HOSTS = 300;

const GRID_COLOR: Record<string, string> = {
  '#10B981': 'bg-emerald-500',
  '#FBBF24': 'bg-amber-400',
  '#EF4444': 'bg-red-500',
  '#64748B': 'bg-slate-500',
  '#F97316': 'bg-orange-500',
};

function FleetOverview({ hosts }: { hosts: HostStat[] }) {
  const problems = useMemo(
    () =>
      hosts
        .filter((h) => !h.host.maintenance && (h.online !== true || h.host.port_error || hostHealth(h) >= 0.2))
        .sort((a, b) => hostHealth(b) - hostHealth(a))
        .slice(0, 50),
    [hosts],
  );
  return (
    <div className="flex flex-col md:flex-row gap-4 px-4 pb-4 pt-14" style={{ height: 380 }}>
      <div className="md:w-72 shrink-0 overflow-y-auto">
        <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-2">
          Needs attention ({problems.length}{problems.length === 50 ? '+' : ''})
        </p>
        {problems.length === 0 ? (
          <p className="text-xs text-emerald-400">All hosts healthy</p>
        ) : (
          <ul className="space-y-0.5">
            {problems.map((h) => (
              <li key={h.host.id}>
                <Link
                  prefetch={false} href={`/hosts/${h.host.id}`}
                  className="flex items-center gap-2 px-2 py-1 rounded text-xs text-slate-300 hover:bg-white/5"
                >
                  <span className={`w-2 h-2 rounded-full shrink-0 ${GRID_COLOR[hostColor(h)] ?? 'bg-slate-500'}`} />
                  <span className="truncate flex-1">{h.host.name || h.host.hostname}</span>
                  <span className="text-[10px] font-mono text-slate-500">
                    {h.online === false ? 'offline' : h.online === null ? 'unknown' : h.host.port_error ? 'port' : h.latency != null ? `${h.latency.toFixed(0)}ms` : ''}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="flex-1 overflow-y-auto">
        <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-2">All hosts</p>
        <div className="flex flex-wrap gap-[3px]">
          {hosts.map((h) => (
            <Link
              key={h.host.id}
              prefetch={false} href={`/hosts/${h.host.id}`}
              title={h.host.name || h.host.hostname}
              aria-label={h.host.name || h.host.hostname}
              className={`w-2.5 h-2.5 rounded-sm hover:ring-1 hover:ring-white/60 ${GRID_COLOR[hostColor(h)] ?? 'bg-slate-500'}`}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

/* ── Main Widget ── */

export interface GravityWidgetProps {
  hosts: HostStat[];
}

export function GravityWidget({ hosts }: GravityWidgetProps) {
  const [isMobile, setIsMobile] = useState(false);
  const [inView, setInView] = useState(true);
  const containerRef = useRef<HTMLDivElement>(null);
  const reducedMotion = usePrefersReducedMotion();
  const largeFleet = hosts.length > GRAVITY_3D_MAX_HOSTS;

  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth < 768);
    check();
    window.addEventListener('resize', check);
    return () => window.removeEventListener('resize', check);
  }, []);

  // Stop rendering the scene while it is scrolled out of view.
  useEffect(() => {
    const el = containerRef.current;
    if (!el || typeof IntersectionObserver === 'undefined') return;
    const obs = new IntersectionObserver(([entry]) => setInView(entry.isIntersecting));
    obs.observe(el);
    return () => obs.disconnect();
  }, [isMobile, largeFleet]);

  const onlineCount = hosts.filter((h) => h.online === true && !h.host.maintenance).length;
  const offlineCount = hosts.filter((h) => h.online === false && !h.host.maintenance).length;
  const unknownCount = hosts.filter((h) => h.online === null && !h.host.maintenance).length;
  const maintCount = hosts.filter((h) => h.host.maintenance).length;

  return (
    <GlassCard className="relative overflow-hidden" style={{ minHeight: isMobile ? 200 : 380 }}>
      {/* HUD overlay — Bento pill badges */}
      <div className="absolute top-4 left-4 z-10 flex items-center gap-2">
        <span className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full" style={{ background: 'var(--ng-card-bg)', border: '1.5px solid var(--ng-card-border)' }}>
          <StatusDot status="online" />
          <span className="text-emerald-400 font-medium">{onlineCount}</span>
        </span>
        <span className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full" style={{ background: 'var(--ng-card-bg)', border: '1.5px solid var(--ng-card-border)' }}>
          <StatusDot status="offline" />
          <span className="text-red-400 font-medium">{offlineCount}</span>
        </span>
        {maintCount > 0 && (
          <span className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full" style={{ background: 'var(--ng-card-bg)', border: '1.5px solid var(--ng-card-border)' }}>
            <StatusDot status="maintenance" />
            <span className="text-amber-400 font-medium">{maintCount}</span>
          </span>
        )}
        {unknownCount > 0 && (
          <span className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full" style={{ background: 'var(--ng-card-bg)', border: '1.5px solid var(--ng-card-border)' }} title="Not observed — nobody is currently checking these hosts">
            <StatusDot status="unknown" />
            <span className="text-slate-400 font-medium">{unknownCount}</span>
          </span>
        )}
        <span className="text-xs text-slate-500 px-2">{hosts.length} total</span>
      </div>

      {!isMobile && !largeFleet && (
        <div className="absolute top-4 right-4 z-10 flex gap-2">
          <span className="text-[10px] text-slate-500 px-2.5 py-1 rounded-full" style={{ background: 'var(--ng-card-bg)', border: '1.5px solid var(--ng-card-border)' }}>Close orbit = healthy</span>
          <span className="text-[10px] text-slate-500 px-2.5 py-1 rounded-full" style={{ background: 'var(--ng-card-bg)', border: '1.5px solid var(--ng-card-border)' }}>Far orbit = degraded</span>
        </div>
      )}

      {isMobile ? (
        <MobileGrid hosts={hosts} />
      ) : largeFleet ? (
        <FleetOverview hosts={hosts} />
      ) : (
        <div ref={containerRef} style={{ height: 380 }}>
          <Canvas
            camera={{ position: [0, 3, 8], fov: 45 }}
            gl={{ antialias: true }}
            dpr={[1, 2]}
            // Reduced motion: a still scene that only redraws on interaction.
            // Out of view: no frames at all.
            frameloop={!inView ? 'never' : reducedMotion ? 'demand' : 'always'}
          >
            <Scene hosts={hosts} animate={!reducedMotion} />
          </Canvas>
        </div>
      )}
    </GlassCard>
  );
}
