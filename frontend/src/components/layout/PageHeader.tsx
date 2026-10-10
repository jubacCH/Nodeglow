'use client';

import type { ReactNode } from 'react';

interface PageHeaderProps {
  title: string;
  /** One line under the title: scope, freshness ("Updated 14:32"). */
  description?: ReactNode;
  /** Right-aligned actions; one primary action at most. */
  actions?: ReactNode;
  /** Status badge or pill before the title (detail pages). */
  status?: ReactNode;
}

/** Page title row (E3 `.phead`): Sora 24px, actions right, wraps on mobile. */
export function PageHeader({ title, description, actions, status }: PageHeaderProps) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <div className="flex min-w-0 items-center gap-2.5">
          {status}
          <h1 className="truncate font-display text-h1 font-semibold tracking-[-0.03em] text-fg max-[759px]:text-[1.5714rem]">
            {title}
          </h1>
        </div>
        {description && <p className="mt-1 text-ui text-fg-2">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
