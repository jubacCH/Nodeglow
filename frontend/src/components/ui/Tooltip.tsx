'use client';

import {
  cloneElement, isValidElement, useEffect, useId, useState,
  type FocusEvent, type MouseEvent, type ReactElement, type ReactNode,
} from 'react';
import { cn } from '@/lib/utils';

type Side = 'top' | 'right' | 'bottom' | 'left';

interface TooltipProps {
  content: ReactNode;
  /** One focusable element (button, link). It gets aria-describedby. */
  children: ReactElement<Record<string, unknown>>;
  side?: Side;
  /** Use the tooltip as the trigger's accessible name instead of a description
   *  (icon-only buttons). The trigger must not have its own aria-label then. */
  asLabel?: boolean;
  className?: string;
}

const position: Record<Side, string> = {
  top: 'bottom-full left-1/2 mb-2 -translate-x-1/2',
  bottom: 'top-full left-1/2 mt-2 -translate-x-1/2',
  right: 'left-full top-1/2 ml-2.5 -translate-y-1/2',
  left: 'right-full top-1/2 mr-2.5 -translate-y-1/2',
};

type Handler<E> = ((e: E) => void) | undefined;

/**
 * Simple accessible tooltip: shows on hover and keyboard focus, hides on
 * Escape, pointer leave and blur. Content is plain text or short markup;
 * never put interactive content or essential information only in a tooltip.
 */
export function Tooltip({ content, children, side = 'top', asLabel, className }: TooltipProps) {
  const id = useId();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  if (!isValidElement(children)) return children;
  const p = children.props;
  const trigger = cloneElement(children, {
    [asLabel ? 'aria-labelledby' : 'aria-describedby']: id,
    onMouseEnter: (e: MouseEvent) => { (p.onMouseEnter as Handler<MouseEvent>)?.(e); setOpen(true); },
    onMouseLeave: (e: MouseEvent) => { (p.onMouseLeave as Handler<MouseEvent>)?.(e); setOpen(false); },
    onFocus: (e: FocusEvent) => {
      (p.onFocus as Handler<FocusEvent>)?.(e);
      // Only keyboard focus opens it (no tooltip flash after a click).
      if ((e.target as HTMLElement).matches?.(':focus-visible')) setOpen(true);
    },
    onBlur: (e: FocusEvent) => { (p.onBlur as Handler<FocusEvent>)?.(e); setOpen(false); },
  });

  return (
    <span className="relative inline-flex">
      {trigger}
      <span
        id={id}
        role="tooltip"
        data-theme="dark"
        className={cn(
          'pointer-events-none absolute z-tooltip whitespace-nowrap rounded-[6px] bg-tip px-[9px] py-[5px] text-meta font-medium text-tip-fg shadow-overlay transition-opacity duration-100',
          position[side],
          open ? 'opacity-100' : 'opacity-0',
          // Hidden tooltips stay in the DOM for aria-describedby but not visible.
          !open && 'invisible',
          className,
        )}
      >
        {content}
      </span>
    </span>
  );
}
