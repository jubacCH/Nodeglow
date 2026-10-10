import { type ClassValue, clsx } from 'clsx';
import { extendTailwindMerge } from 'tailwind-merge';

// Teach tailwind-merge the design-system scale (tailwind.config.ts) so that
// e.g. `text-meta` (size) and `text-fg-2` (colour) do not cancel each other.
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      'font-size': [{ text: ['micro', 'meta', 'ui', 'body', 'lead', 'h3', 'h1', 'num-sm', 'num', 'num-lg'] }],
      shadow: [{ shadow: ['overlay', 'ng-sm', 'glow-crit', 'glow-warn', 'glow-dot-crit', 'glow-dot-warn', 'accent-glow'] }],
      rounded: [{ rounded: ['card', 'ctl', 'chip', 'ng-sm', 'ng-lg', 'pill'] }],
    },
  },
});

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
  if (pct === null) return 'text-fg-3';
  if (pct >= 99.9) return 'text-ok';
  if (pct >= 95) return 'text-degraded';
  return 'text-down';
}

export function timeAgo(dateStr: string | null | undefined): string {
  if (!dateStr) return '—';
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diff = Math.max(0, now - then);
  const s = Math.floor(diff / 1000);
  if (s < 60) return 'just now';
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d}d ago`;
  return `${Math.floor(d / 30)}mo ago`;
}

export function severityColor(severity: 'critical' | 'warning' | 'info' | string): string {
  switch (severity) {
    case 'critical': return 'bg-down-soft text-down border-down/30';
    case 'warning': return 'bg-warning-soft text-warning border-warning/30';
    case 'info': return 'bg-maint-soft text-maint border-maint/30';
    default: return 'bg-unknown-soft text-unknown border-border-2';
  }
}
