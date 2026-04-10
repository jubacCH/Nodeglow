'use client';

import type { ReactNode } from 'react';

interface PageHeaderProps {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}

export function PageHeader({ title, description, actions }: PageHeaderProps) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between mb-6 sm:mb-8">
      <div className="min-w-0">
        <h1 className="text-2xl sm:text-3xl font-bold text-slate-100 tracking-tight truncate">{title}</h1>
        {description && (
          <p className="mt-1.5 text-sm text-slate-400">{description}</p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2 sm:gap-3 flex-wrap">{actions}</div>}
    </div>
  );
}
