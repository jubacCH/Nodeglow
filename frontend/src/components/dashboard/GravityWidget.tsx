'use client';

import { useState, useEffect, useMemo, useRef } from 'react';
import { GlassCard } from '@/components/ui/GlassCard';
import { StatusDot } from '@/components/ui/StatusDot';
import Link from 'next/link';
import type { HostStat } from '@/hooks/queries/useDashboard';

function useIsMobile() {
  const [mobile, setMobile] = useState(false);
  useEffect(() => {
    const check = () => setMobile(window.innerWidth < 768);
    check();
    window.addEventListener('resize', check);
    return () => window.removeEventListener('resize', check);
  }, []);
  return mobile;
}

/* ── Health score per host ── */

function hostHealth(h: HostStat): number {
  if (h.host.maintenance) return 0.5;
  if (h.online === false) return 1.0;
  if (h.online === null) return 0.8;
  // Use latency + uptime as health proxy
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
  if (h.online === false) return '#F87171';
  if (h.online === null) return '#64748B';
  const health = hostHealth(h);
  if (health >= 0.5) return '#F87171';
  if (health >= 0.2) return '#FBBF24';
  return '#34D399';
}

function hostGlow(h: HostStat): string {
  if (h.online === false) return 'rgba(248,113,113,0.6)';
  if (h.host.maintenance) return 'rgba(251,191,36,0.3)';
  return 'rgba(52,211,153,0.3)';
}

/* ── Orbital layout ── */

interface PlacedHost {
  host: HostStat;
  x: number;
  y: number;
  ring: number;
  health: number;
  color: string;
}

function layoutHosts(hosts: HostStat[], cx: number, cy: number, maxRadius: number): PlacedHost[] {
  // Sort by health: healthiest first (inner ring)
  const sorted = [...hosts].sort((a, b) => hostHealth(a) - hostHealth(b));

  const ringCount = Math.max(2, Math.ceil(Math.sqrt(hosts.length / 6)));
  const ringGap = maxRadius / (ringCount + 0.5);
  const minRing = ringGap * 1.2;

  const result: PlacedHost[] = [];
  let idx = 0;

  for (let ring = 0; ring < ringCount && idx < sorted.length; ring++) {
    const radius = minRing + ring * ringGap;
    const circumference = 2 * Math.PI * radius;
    const dotSize = 12;
    const maxOnRing = Math.max(4, Math.floor(circumference / (dotSize + 8)));
    const countOnRing = Math.min(maxOnRing, sorted.length - idx);
    const angleStep = (2 * Math.PI) / countOnRing;
    const angleOffset = ring * 0.4; // Stagger rings

    for (let i = 0; i < countOnRing; i++) {
      const h = sorted[idx];
      const angle = angleOffset + i * angleStep;
      result.push({
        host: h,
        x: cx + Math.cos(angle) * radius,
        y: cy + Math.sin(angle) * radius,
        ring,
        health: hostHealth(h),
        color: hostColor(h),
      });
      idx++;
    }
  }

  return result;
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
            href={`/hosts/${h.host.id}`}
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

/* ── Tooltip ── */

function Tooltip({ host, x, y }: { host: HostStat; x: number; y: number }) {
  return (
    <div
      className="absolute z-20 pointer-events-none bg-[#0B0E14]/95 border border-white/[0.08] rounded-lg px-3 py-2 shadow-xl"
      style={{ left: x + 16, top: y - 10 }}
    >
      <p className="text-xs font-medium text-slate-200">{host.host.name}</p>
      <p className="text-[10px] text-slate-500 font-mono">{host.host.hostname}</p>
      <div className="flex items-center gap-3 mt-1">
        <span className="text-[10px] text-slate-400">
          {host.online === null ? 'Unknown' : host.online ? 'Online' : 'Offline'}
        </span>
        {host.latency != null && (
          <span className="text-[10px] font-mono text-slate-400">{host.latency.toFixed(0)}ms</span>
        )}
        {host.uptime_stats?.h24 != null && (
          <span className="text-[10px] font-mono text-slate-400">{host.uptime_stats.h24.toFixed(1)}%</span>
        )}
      </div>
    </div>
  );
}

/* ── Main Widget ── */

export interface GravityWidgetProps {
  hosts: HostStat[];
}

export function GravityWidget({ hosts }: GravityWidgetProps) {
  const isMobile = useIsMobile();
  const containerRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ width: 600, height: 360 });
  const [hovered, setHovered] = useState<{ host: HostStat; x: number; y: number } | null>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect;
      setDimensions({ width, height: Math.max(height, 300) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const onlineCount = hosts.filter((h) => h.online === true && !h.host.maintenance).length;
  const offlineCount = hosts.filter((h) => h.online === false && !h.host.maintenance).length;
  const maintCount = hosts.filter((h) => h.host.maintenance).length;

  const cx = dimensions.width / 2;
  const cy = dimensions.height / 2;
  const maxRadius = Math.min(cx, cy) - 24;

  const placed = useMemo(
    () => layoutHosts(hosts, cx, cy, maxRadius),
    [hosts, cx, cy, maxRadius],
  );

  // Concentric guide rings
  const ringCount = Math.max(2, Math.ceil(Math.sqrt(hosts.length / 6)));
  const ringGap = maxRadius / (ringCount + 0.5);
  const guideRings = Array.from({ length: ringCount }, (_, i) => ringGap * 1.2 + i * ringGap);

  return (
    <GlassCard className="relative overflow-hidden" style={{ minHeight: isMobile ? 200 : 360 }}>
      {/* HUD counters */}
      <div className="absolute top-3 left-3 z-10 flex items-center gap-3">
        <span className="flex items-center gap-1.5 text-xs">
          <StatusDot status="online" />
          <span className="text-emerald-400 font-medium">{onlineCount}</span>
        </span>
        <span className="flex items-center gap-1.5 text-xs">
          <StatusDot status="offline" />
          <span className="text-red-400 font-medium">{offlineCount}</span>
        </span>
        {maintCount > 0 && (
          <span className="flex items-center gap-1.5 text-xs">
            <StatusDot status="maintenance" />
            <span className="text-amber-400 font-medium">{maintCount}</span>
          </span>
        )}
        <span className="text-xs text-slate-500">{hosts.length} total</span>
      </div>

      {/* Legend */}
      <div className="absolute top-3 right-3 z-10 flex gap-3 text-[10px] text-slate-500">
        <span>Inner = healthy</span>
        <span>Outer = degraded</span>
      </div>

      {isMobile ? (
        <MobileGrid hosts={hosts} />
      ) : (
        <div ref={containerRef} className="w-full" style={{ minHeight: 360 }}>
          <svg
            width={dimensions.width}
            height={dimensions.height}
            className="w-full"
            viewBox={`0 0 ${dimensions.width} ${dimensions.height}`}
          >
            <defs>
              {/* Glow filter */}
              <filter id="glow">
                <feGaussianBlur stdDeviation="3" result="blur" />
                <feMerge>
                  <feMergeNode in="blur" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
              {/* Radial gradient background */}
              <radialGradient id="bgGrad" cx="50%" cy="50%" r="50%">
                <stop offset="0%" stopColor="rgba(56,189,248,0.03)" />
                <stop offset="100%" stopColor="rgba(0,0,0,0)" />
              </radialGradient>
            </defs>

            {/* Background glow */}
            <circle cx={cx} cy={cy} r={maxRadius} fill="url(#bgGrad)" />

            {/* Guide rings */}
            {guideRings.map((r, i) => (
              <circle
                key={i}
                cx={cx}
                cy={cy}
                r={r}
                fill="none"
                stroke="rgba(255,255,255,0.03)"
                strokeWidth={1}
                strokeDasharray="4 6"
              />
            ))}

            {/* Center dot */}
            <circle cx={cx} cy={cy} r={3} fill="rgba(56,189,248,0.4)" />
            <circle cx={cx} cy={cy} r={1.5} fill="#38BDF8" />

            {/* Host dots */}
            {placed.map((p) => (
              <Link key={p.host.host.id} href={`/hosts/${p.host.host.id}`}>
                <g
                  onMouseEnter={(e) => setHovered({ host: p.host, x: e.clientX - (containerRef.current?.getBoundingClientRect().left ?? 0), y: e.clientY - (containerRef.current?.getBoundingClientRect().top ?? 0) })}
                  onMouseLeave={() => setHovered(null)}
                  className="cursor-pointer"
                >
                  {/* Glow */}
                  <circle
                    cx={p.x}
                    cy={p.y}
                    r={8}
                    fill={hostGlow(p.host)}
                    opacity={p.host.online === false ? 0.8 : 0.4}
                    filter="url(#glow)"
                  />
                  {/* Dot */}
                  <circle
                    cx={p.x}
                    cy={p.y}
                    r={5}
                    fill={p.color}
                    stroke="rgba(255,255,255,0.1)"
                    strokeWidth={0.5}
                    className="transition-all duration-300 hover:r-[7]"
                  />
                  {/* Pulse for offline */}
                  {p.host.online === false && (
                    <circle
                      cx={p.x}
                      cy={p.y}
                      r={5}
                      fill="none"
                      stroke={p.color}
                      strokeWidth={1}
                      opacity={0.5}
                    >
                      <animate
                        attributeName="r"
                        values="5;12;5"
                        dur="2s"
                        repeatCount="indefinite"
                      />
                      <animate
                        attributeName="opacity"
                        values="0.5;0;0.5"
                        dur="2s"
                        repeatCount="indefinite"
                      />
                    </circle>
                  )}
                </g>
              </Link>
            ))}
          </svg>

          {/* Tooltip */}
          {hovered && (
            <Tooltip host={hovered.host} x={hovered.x} y={hovered.y} />
          )}
        </div>
      )}
    </GlassCard>
  );
}
