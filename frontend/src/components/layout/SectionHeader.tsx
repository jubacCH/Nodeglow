'use client';

import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface SectionHeaderProps {
  /** Section title, between PageHeader (h1) and CardHeader (h2/h3). */
  title: string;
  subtitle?: string;
  icon?: LucideIcon;
  /** @deprecated Section icons are neutral in E3; ignored. */
  iconColor?: string;
  actions?: ReactNode;
  className?: string;
}

/**
 * In-page section divider for pages with several logical blocks.
 *   PageHeader    → Sora 24px semibold   (h1)
 *   SectionHeader → 17px medium          (h2) ← this
 *   CardHeader    → 14px medium          (h2/h3 inside a card)
 */
export function SectionHeader({ title, subtitle, icon: Icon, actions, className }: SectionHeaderProps) {
  return (
    <div className={cn('mb-3 mt-6 flex items-center justify-between gap-3 border-b border-border pb-2', className)}>
      <div className="flex min-w-0 items-baseline gap-2">
        {Icon && <Icon size={15} className="self-center text-fg-3" aria-hidden="true" />}
        <h2 className="truncate text-lead font-medium tracking-[-0.01em] text-fg">{title}</h2>
        {subtitle && <span className="truncate text-meta text-fg-3">{subtitle}</span>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}
