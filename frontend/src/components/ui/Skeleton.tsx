'use client';

import { cn } from '@/lib/utils';

interface SkeletonProps {
  className?: string;
}

/** Loading placeholder in the shape of the content (never show zeros while loading). */
export function Skeleton({ className }: SkeletonProps) {
  return <div aria-hidden="true" className={cn('ng-shimmer rounded-ng-sm', className)} />;
}
