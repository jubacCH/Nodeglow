'use client';

import Link from 'next/link';
import { ChevronRight, LayoutDashboard } from 'lucide-react';

export interface Crumb {
  label: string;
  href?: string;
}

interface BreadcrumbsProps {
  items: Crumb[];
}

export function Breadcrumbs({ items }: BreadcrumbsProps) {
  return (
    <nav className="flex items-center gap-1.5 text-sm mb-4">
      <Link
        href="/"
        className="text-slate-400 hover:text-slate-200 transition-colors flex items-center"
      >
        <LayoutDashboard size={14} />
      </Link>
      {items.map((item, i) => {
        const isLast = i === items.length - 1;
        return (
          <span key={i} className="flex items-center gap-1.5">
            <ChevronRight size={14} className="text-slate-600" />
            {item.href && !isLast ? (
              <Link
                href={item.href}
                className="text-slate-400 hover:text-slate-200 transition-colors"
              >
                {item.label}
              </Link>
            ) : (
              <span className={isLast ? 'text-slate-200' : 'text-slate-400'}>
                {item.label}
              </span>
            )}
          </span>
        );
      })}
    </nav>
  );
}
