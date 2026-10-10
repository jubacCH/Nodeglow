'use client';

import { useCallback, useEffect, useId, useRef, useState, type ReactNode, type Ref } from 'react';
import { cn } from '@/lib/utils';
import { focusableIn } from '@/hooks/useModalBehavior';

export interface PopoverTriggerProps {
  ref: Ref<HTMLButtonElement>;
  onClick: () => void;
  'aria-expanded': boolean;
  'aria-controls': string;
  'aria-haspopup': 'dialog';
}

type Placement = 'bottom-end' | 'bottom-start' | 'right-start' | 'top-end';

const place: Record<Placement, string> = {
  'bottom-end': 'right-0 top-full mt-2',
  'bottom-start': 'left-0 top-full mt-2',
  'right-start': 'left-full top-0 ml-2',
  'top-end': 'bottom-full right-0 mb-2',
};

interface PopoverProps {
  /** Render the trigger button; spread the props onto it. */
  trigger: (props: PopoverTriggerProps) => ReactNode;
  /** Accessible name of the popover dialog. */
  label: string;
  children: ReactNode | ((close: () => void) => ReactNode);
  placement?: Placement;
  className?: string;
}

/**
 * Small non-modal popover (about, tenant, user menu). Opens on click, closes
 * on Escape, outside click or when focus leaves; focus moves into it and
 * back to the trigger.
 */
export function Popover({ trigger, label, children, placement = 'bottom-end', className }: PopoverProps) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);

  const close = useCallback((returnFocus = true) => {
    setOpen(false);
    if (returnFocus) triggerRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!open) return;
    const panel = panelRef.current;
    requestAnimationFrame(() => (panel && (focusableIn(panel)[0] ?? panel))?.focus());
    const onDown = (e: PointerEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) close(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopPropagation(); close(); }
    };
    const onFocus = (e: FocusEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', onDown);
    document.addEventListener('keydown', onKey);
    document.addEventListener('focusin', onFocus);
    return () => {
      document.removeEventListener('pointerdown', onDown);
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('focusin', onFocus);
    };
  }, [open, close]);

  return (
    <div ref={wrapRef} className="relative">
      {trigger({
        ref: triggerRef,
        onClick: () => setOpen((v) => !v),
        'aria-expanded': open,
        'aria-controls': id,
        'aria-haspopup': 'dialog',
      })}
      <div
        ref={panelRef}
        id={id}
        role="dialog"
        aria-label={label}
        tabIndex={-1}
        hidden={!open}
        className={cn(
          'absolute z-popover w-[300px] rounded-card border border-border-2 bg-surface p-1.5 text-fg shadow-overlay outline-none',
          place[placement],
          className,
        )}
      >
        {open && (typeof children === 'function' ? children(() => close()) : children)}
      </div>
    </div>
  );
}

/** Row inside a popover menu (button or link look). */
export function PopoverItem({
  icon, children, onClick, current, className,
}: { icon?: ReactNode; children: ReactNode; onClick?: () => void; current?: boolean; className?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={current || undefined}
      className={cn(
        'flex w-full items-center gap-2.5 rounded-ng-sm px-2.5 py-[9px] text-left text-ui text-fg hover:bg-surface-2',
        current && 'bg-surface-2',
        className,
      )}
    >
      {icon && <span className="grid h-5 w-5 place-items-center text-fg-2" aria-hidden="true">{icon}</span>}
      <span className="min-w-0 flex-1">{children}</span>
    </button>
  );
}
