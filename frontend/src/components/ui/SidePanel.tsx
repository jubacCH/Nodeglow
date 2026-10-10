'use client';

import { useId, useRef, type ReactNode, type RefObject } from 'react';
import { createPortal } from 'react-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { X } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useModalBehavior } from '@/hooks/useModalBehavior';
import { useIsClient } from './Modal';

export interface SidePanelProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  /** One line under the title: state reason, source, freshness. */
  meta?: ReactNode;
  /** Leading visual (object glyph with status dot). */
  icon?: ReactNode;
  children: ReactNode;
  /** Action row at the bottom (primary action first). */
  footer?: ReactNode;
  /** 460px (default) up to 640px; full width on phones. */
  width?: 'md' | 'lg';
  /** Accessible name when `title` is not plain text. */
  ariaLabel?: string;
  initialFocus?: RefObject<HTMLElement | null>;
}

/**
 * Right-hand drawer (IA §5.4) for previewing an object and acting quickly.
 * Escape closes, focus is trapped and returns to the trigger, the page behind
 * is inert. Never stack two panels: replace the content instead.
 */
export function SidePanel({
  open, onClose, title, meta, icon, children, footer, width = 'md', ariaLabel, initialFocus,
}: SidePanelProps) {
  const titleId = useId();
  const ref = useRef<HTMLDivElement>(null);
  const isClient = useIsClient();
  useModalBehavior(open, ref, onClose, { initialFocus });

  if (!isClient) return null;
  return createPortal(
    <AnimatePresence>
      {open && (
        <div className="fixed inset-0 z-panel">
          <motion.div
            className="absolute inset-0 bg-scrim"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            onClick={onClose}
            aria-hidden="true"
          />
          <motion.aside
            ref={ref}
            role="dialog"
            aria-modal="true"
            aria-labelledby={ariaLabel ? undefined : titleId}
            aria-label={ariaLabel}
            tabIndex={-1}
            className={cn(
              'absolute inset-y-0 right-0 flex w-full flex-col border-l border-border bg-surface shadow-overlay outline-none',
              width === 'md' ? 'max-w-[460px]' : 'max-w-[640px]',
            )}
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ duration: 0.22, ease: [0.2, 0.7, 0.2, 1] }}
          >
            <header className="flex items-start gap-3 border-b border-border px-5 pb-4 pt-[18px]">
              {icon && <div className="shrink-0">{icon}</div>}
              <div className="min-w-0 flex-1">
                <h2 id={titleId} className="break-words font-display text-[1.2143rem] font-semibold leading-[1.3] tracking-[-0.025em] text-fg">
                  {title}
                </h2>
                {meta && <div className="mt-[3px] text-meta text-fg-2">{meta}</div>}
              </div>
              <button
                type="button"
                onClick={onClose}
                aria-label="Close panel"
                className="-mr-1 grid h-9 w-9 shrink-0 place-items-center rounded-ctl text-fg-2 hover:bg-surface-2 hover:text-fg"
              >
                <X size={18} aria-hidden="true" />
              </button>
            </header>
            <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 pb-6 pt-1">{children}</div>
            {footer && <footer className="flex flex-wrap gap-2 border-t border-border px-5 py-3.5">{footer}</footer>}
          </motion.aside>
        </div>
      )}
    </AnimatePresence>,
    document.body,
  );
}

/** Section inside a SidePanel body (E3 `.ps`). */
export function PanelSection({ title, children, className }: { title?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cn('border-b border-border py-4 last:border-b-0', className)}>
      {title && <h3 className="mb-2.5 text-meta font-medium text-fg-2">{title}</h3>}
      {children}
    </section>
  );
}
