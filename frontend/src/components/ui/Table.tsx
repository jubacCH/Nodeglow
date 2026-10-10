'use client';

import { forwardRef, type HTMLAttributes, type ReactNode, type TdHTMLAttributes, type ThHTMLAttributes } from 'react';
import { ArrowDown, ArrowUp, ArrowUpDown } from 'lucide-react';
import { cn } from '@/lib/utils';

/**
 * Table primitives (IA §6.1): status column first, numeric columns right
 * aligned with tabular numerals, whole row clickable via a link in the first
 * cell, sticky header optional, comfortable (36px) or compact (28px) rows.
 *
 *   <TableContainer><Table density="compact">
 *     <THead sticky><Tr><Th>Host</Th><Th numeric sort="desc" onSort={…}>Latency</Th></Tr></THead>
 *     <TBody><Tr interactive><Td>…</Td><Td numeric>12 ms</Td></Tr></TBody>
 *   </Table></TableContainer>
 */
export function TableContainer({ children, className, maxHeight }: { children: ReactNode; className?: string; maxHeight?: number | string }) {
  return (
    <div className={cn('w-full overflow-auto', className)} style={maxHeight ? { maxHeight } : undefined}>
      {children}
    </div>
  );
}

interface TableProps extends HTMLAttributes<HTMLTableElement> {
  density?: 'comfortable' | 'compact';
}

export const Table = forwardRef<HTMLTableElement, TableProps>(({ density = 'comfortable', className, ...props }, ref) => (
  <table
    ref={ref}
    data-density={density}
    className={cn('group/table w-full border-collapse text-left text-ui', className)}
    {...props}
  />
));
Table.displayName = 'Table';

export function THead({ sticky, className, ...props }: HTMLAttributes<HTMLTableSectionElement> & { sticky?: boolean }) {
  return (
    <thead
      className={cn(sticky && '[&_th]:sticky [&_th]:top-0 [&_th]:z-[1] [&_th]:bg-surface', className)}
      {...props}
    />
  );
}

export function TBody(props: HTMLAttributes<HTMLTableSectionElement>) {
  return <tbody {...props} />;
}

export function Tr({ interactive, selected, className, ...props }: HTMLAttributes<HTMLTableRowElement> & { interactive?: boolean; selected?: boolean }) {
  return (
    <tr
      aria-selected={selected || undefined}
      className={cn(
        'border-b border-border last:border-b-0',
        interactive && 'cursor-pointer hover:bg-surface-2',
        selected && 'bg-accent-soft hover:bg-accent-soft',
        className,
      )}
      {...props}
    />
  );
}

interface ThProps extends ThHTMLAttributes<HTMLTableCellElement> {
  numeric?: boolean;
  /** Current sort of this column; renders a sort button and aria-sort. */
  sort?: 'asc' | 'desc' | 'none';
  onSort?: () => void;
}

export function Th({ numeric, sort, onSort, className, children, ...props }: ThProps) {
  const ariaSort = sort === 'asc' ? 'ascending' : sort === 'desc' ? 'descending' : sort === 'none' ? 'none' : undefined;
  const Icon = sort === 'asc' ? ArrowUp : sort === 'desc' ? ArrowDown : ArrowUpDown;
  return (
    <th
      scope="col"
      aria-sort={ariaSort}
      className={cn(
        'h-[36px] whitespace-nowrap border-b border-border px-3 text-meta font-medium text-fg-2',
        'group-data-[density=compact]/table:h-[30px]',
        numeric && 'text-right',
        className,
      )}
      {...props}
    >
      {onSort ? (
        <button
          type="button"
          onClick={onSort}
          className={cn('inline-flex items-center gap-1 rounded-chip hover:text-fg', numeric && 'flex-row-reverse')}
        >
          {children}
          <Icon size={12} aria-hidden="true" className={sort && sort !== 'none' ? 'text-fg' : 'text-fg-3'} />
        </button>
      ) : (
        children
      )}
    </th>
  );
}

export function Td({ numeric, muted, className, ...props }: TdHTMLAttributes<HTMLTableCellElement> & { numeric?: boolean; muted?: boolean }) {
  return (
    <td
      className={cn(
        'h-[36px] px-3 align-middle text-fg',
        'group-data-[density=compact]/table:h-[28px] group-data-[density=compact]/table:py-0',
        numeric && 'num text-right',
        muted && 'text-fg-2',
        className,
      )}
      {...props}
    />
  );
}
