'use client';

import { cn } from '@/lib/utils';

interface SkeletonProps {
  className?: string;
}

export function Skeleton({ className }: SkeletonProps) {
  return (
    <div
      className={cn(
        'ng-shimmer rounded-md bg-white/[0.04]',
        className,
      )}
    />
  );
}
