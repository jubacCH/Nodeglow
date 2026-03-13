'use client';

import { cn } from '@/lib/utils';

interface StatusDotProps {
  status: 'online' | 'offline' | 'maintenance' | 'unknown' | 'disabled';
  pulse?: boolean;
  className?: string;
}

const dotColors: Record<StatusDotProps['status'], string> = {
  online: 'bg-emerald-400',
  offline: 'bg-red-400',
  maintenance: 'bg-amber-400',
  unknown: 'bg-slate-500',
  disabled: 'bg-slate-600',
};

const glowColors: Record<StatusDotProps['status'], string> = {
  online: 'shadow-emerald-400/50',
  offline: 'shadow-red-400/50',
  maintenance: 'shadow-amber-400/50',
  unknown: '',
  disabled: '',
};

export function StatusDot({ status, pulse, className }: StatusDotProps) {
  return (
    <span
      className={cn(
        'inline-block w-2.5 h-2.5 rounded-full',
        dotColors[status],
        pulse && status === 'offline' && 'animate-pulse',
        pulse && glowColors[status] && `shadow-[0_0_6px_2px] ${glowColors[status]}`,
        className,
      )}
    />
  );
}
