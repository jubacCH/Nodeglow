import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export function formatLatency(ms: number | null): string {
  if (ms === null) return '—';
  if (ms < 1) return '<1ms';
  return `${Math.round(ms)}ms`;
}

export function uptimeColor(pct: number | null): string {
  if (pct === null) return 'text-slate-500';
  if (pct >= 99.9) return 'text-emerald-400';
  if (pct >= 95) return 'text-amber-400';
  return 'text-red-400';
}

export function severityColor(severity: 'critical' | 'warning' | 'info' | string): string {
  switch (severity) {
    case 'critical': return 'bg-red-500/20 text-red-400 border-red-500/30';
    case 'warning': return 'bg-amber-500/20 text-amber-400 border-amber-500/30';
    case 'info': return 'bg-blue-500/20 text-blue-400 border-blue-500/30';
    default: return 'bg-slate-500/20 text-slate-400 border-slate-500/30';
  }
}
