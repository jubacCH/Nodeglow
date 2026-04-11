'use client';

import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface SectionHeaderProps {
  /** Section title. Sits between PageHeader (text-3xl) and widget labels
   *  (text-[10px] uppercase) in the hierarchy — fills the missing middle tier. */
  title: string;
  /** Optional muted subtitle shown to the right of the title */
  subtitle?: string;
  /** Optional icon rendered before the title, matched to the section theme */
  icon?: LucideIcon;
  /** Accent color for the icon (Tailwind class like "text-sky-400") */
  iconColor?: string;
  /** Right-hand content: actions, counts, filters */
  actions?: ReactNode;
  className?: string;
}

/**
 * In-page section divider. Use when a single page has multiple logical
 * sections ("Recent Activity", "Historical Trends", "Settings") and the
 * eye needs a scan anchor that's bigger than a card header but smaller
 * than the page title.
 *
 * Typography hierarchy:
 *   PageHeader     → text-2xl/3xl font-bold           ← top of page
 *   SectionHeader  → text-lg     font-semibold         ← this component
 *   WidgetHeader   → text-[10px] uppercase widest      ← inside a card
 */
export function SectionHeader({
  title,
  subtitle,
  icon: Icon,
  iconColor = 'text-slate-400',
  actions,
  className,
}: SectionHeaderProps) {
  return (
    <div
      className={cn(
        'flex items-center justify-between gap-3 mt-6 mb-3 pb-2 border-b',
        className,
      )}
      style={{ borderColor: 'var(--ng-card-border)' }}
    >
      <div className="flex items-baseline gap-2 min-w-0">
        {Icon && <Icon size={15} className={cn(iconColor, 'self-center')} />}
        <h2 className="text-lg font-semibold text-slate-200 tracking-tight truncate">
          {title}
        </h2>
        {subtitle && (
          <span className="text-xs text-slate-500 truncate">{subtitle}</span>
        )}
      </div>
      {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
    </div>
  );
}
