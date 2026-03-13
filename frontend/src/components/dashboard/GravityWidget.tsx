'use client';

import { useState, useEffect, useCallback } from 'react';
import { GlassCard } from '@/components/ui/GlassCard';
import { StatusDot } from '@/components/ui/StatusDot';
import { GravityGlobe } from './GravityGlobe';
import type { HostStat } from '@/hooks/queries/useDashboard';

type CameraPreset = 'top' | 'front' | 'free';

const CAMERA_POSITIONS: Record<CameraPreset, [number, number, number]> = {
  top: [0, 8, 0.1],
  front: [0, 0, 7],
  free: [0, 2, 7],
};

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

/* ── Mobile fallback grid ── */

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
          <a
            key={h.host.id}
            href={`/hosts/${h.host.id}`}
            className="flex flex-col items-center gap-1 p-2 rounded-md hover:bg-white/5 transition-colors"
          >
            <StatusDot status={status} pulse={status === 'offline'} />
            <span className="text-[10px] text-slate-400 truncate max-w-full">
              {h.host.name}
            </span>
          </a>
        );
      })}
    </div>
  );
}

/* ── Widget ── */

export interface GravityWidgetProps {
  hosts: HostStat[];
}

export function GravityWidget({ hosts }: GravityWidgetProps) {
  const isMobile = useIsMobile();
  const [preset, setPreset] = useState<CameraPreset>('free');

  const onlineCount = hosts.filter((h) => h.online === true && !h.host.maintenance).length;
  const offlineCount = hosts.filter((h) => h.online === false && !h.host.maintenance).length;
  const totalCount = hosts.length;

  const handlePreset = useCallback((p: CameraPreset) => setPreset(p), []);

  return (
    <GlassCard className="relative overflow-hidden" style={{ minHeight: isMobile ? 200 : 400 }}>
      {/* HUD: counters (top-left) */}
      <div className="absolute top-3 left-3 z-10 flex items-center gap-3">
        <span className="flex items-center gap-1.5 text-xs">
          <StatusDot status="online" />
          <span className="text-emerald-400 font-medium">{onlineCount}</span>
        </span>
        <span className="flex items-center gap-1.5 text-xs">
          <StatusDot status="offline" />
          <span className="text-red-400 font-medium">{offlineCount}</span>
        </span>
        <span className="text-xs text-slate-500">{totalCount} total</span>
      </div>

      {/* HUD: camera presets (top-right) */}
      {!isMobile && (
        <div className="absolute top-3 right-3 z-10 flex gap-1">
          {(['top', 'front', 'free'] as const).map((p) => (
            <button
              key={p}
              onClick={() => handlePreset(p)}
              className={`px-2 py-0.5 text-[10px] rounded border transition-colors ${
                preset === p
                  ? 'bg-white/10 border-white/20 text-white'
                  : 'bg-transparent border-white/5 text-slate-500 hover:text-slate-300 hover:border-white/10'
              }`}
            >
              {p.charAt(0).toUpperCase() + p.slice(1)}
            </button>
          ))}
        </div>
      )}

      {/* Content */}
      {isMobile ? (
        <MobileGrid hosts={hosts} />
      ) : (
        <div className="w-full h-full" style={{ minHeight: 400 }}>
          <GravityGlobe hosts={hosts} cameraPosition={CAMERA_POSITIONS[preset]} />
        </div>
      )}
    </GlassCard>
  );
}
