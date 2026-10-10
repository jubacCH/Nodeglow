import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

/** Keyboard key. Combos: <Kbd>Ctrl</Kbd> <Kbd>K</Kbd> or <Kbd>Ctrl K</Kbd>. */
export function Kbd({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <kbd
      className={cn(
        'inline-flex h-[20px] min-w-[20px] items-center justify-center whitespace-nowrap rounded-chip border border-border-2 bg-surface-2 px-1.5 font-sans text-micro font-medium text-fg-2',
        className,
      )}
    >
      {children}
    </kbd>
  );
}
