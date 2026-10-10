'use client';

import Link from 'next/link';
import { ChevronRight } from 'lucide-react';

export interface Crumb {
  label: string;
  href?: string;
}

interface BreadcrumbsProps {
  items: Crumb[];
}

/**
 * Breadcrumbs (IA §5.3): `Section › Page › Object › Tab`, text only (no home
 * icon), last item is the current page.
 */
export function Breadcrumbs({ items }: BreadcrumbsProps) {
  return (
    <nav aria-label="Breadcrumb" className="mb-3">
      <ol className="flex flex-wrap items-center gap-1.5 text-meta">
        {items.map((item, i) => {
          const isLast = i === items.length - 1;
          return (
            <li key={i} className="flex min-w-0 items-center gap-1.5">
              {i > 0 && <ChevronRight size={12} className="shrink-0 text-fg-3" aria-hidden="true" />}
              {item.href && !isLast ? (
                <Link href={item.href} className="truncate text-fg-2 hover:text-fg">
                  {item.label}
                </Link>
              ) : (
                <span className={isLast ? 'truncate text-fg' : 'truncate text-fg-2'} aria-current={isLast ? 'page' : undefined}>
                  {item.label}
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
