'use client';

import { forwardRef, type HTMLAttributes, type ReactNode } from 'react';
import { cn } from '@/lib/utils';

export interface CardProps extends HTMLAttributes<HTMLElement> {
  /** Render as <section> (with a heading inside) or <div>/<article>. */
  as?: 'div' | 'section' | 'article';
  padding?: 'none' | 'sm' | 'md';
  /** Hover border for cards that are links or open a panel. */
  interactive?: boolean;
  /**
   * Attention glow around the card: `crit` (strong, breathes in dark mode),
   * `warn` (soft, static). Only for objects that need attention — never for
   * healthy, maintenance or unknown content.
   */
  glow?: 'crit' | 'warn';
  /** Acknowledged critical: keeps half the halo, stops breathing. */
  acknowledged?: boolean;
}

const pad = { none: '', sm: 'p-[16px]', md: 'p-[20px] max-[759px]:p-[16px]' } as const;

/** The basic surface: flat, 1px border, 10px radius, no shadow. */
export const Card = forwardRef<HTMLElement, CardProps>(
  ({ as: As = 'div', padding = 'md', interactive, glow, acknowledged, className, children, ...props }, ref) => (
    <As
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      ref={ref as any}
      className={cn(
        'min-w-0 rounded-card border border-border bg-surface',
        pad[padding],
        interactive && 'transition-colors duration-150 hover:border-border-2',
        glow === 'crit' && 'ng-glow-crit',
        glow === 'warn' && 'ng-glow-warn',
        glow && acknowledged && 'ng-glow-acked',
        className,
      )}
      {...props}
    >
      {children}
    </As>
  ),
);
Card.displayName = 'Card';

export interface CardHeaderProps {
  title: ReactNode;
  /** id for the heading, so the card can use aria-labelledby. */
  titleId?: string;
  /** Muted text right of the title or below it. */
  meta?: ReactNode;
  /** Right-aligned actions (links, small buttons, pills). */
  actions?: ReactNode;
  /** Heading level; cards on a page are usually h2. */
  level?: 2 | 3;
  className?: string;
}

/** Card title row: 14px medium title, actions right (E3 `.ch`). */
export function CardHeader({ title, titleId, meta, actions, level = 2, className }: CardHeaderProps) {
  const H = level === 2 ? 'h2' : 'h3';
  return (
    <div className={cn('mb-[16px] flex min-h-[22px] items-center justify-between gap-3', className)}>
      <div className="flex min-w-0 items-baseline gap-2">
        <H id={titleId} className="truncate text-body font-medium text-fg">{title}</H>
        {meta && <span className="truncate text-meta text-fg-3">{meta}</span>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2 text-meta text-fg-3">{actions}</div>}
    </div>
  );
}
