'use client';

import { useId, useRef, useSyncExternalStore, type ReactNode, type RefObject } from 'react';
import { createPortal } from 'react-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { X } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useModalBehavior } from '@/hooks/useModalBehavior';

const subscribeNoop = () => () => {};
/** True after hydration (portals need document.body). */
export function useIsClient() {
  return useSyncExternalStore(subscribeNoop, () => true, () => false);
}

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  /** Optional text under the title (wired to aria-describedby). */
  description?: ReactNode;
  children: ReactNode;
  /** Button row at the bottom (right-aligned). */
  footer?: ReactNode;
  size?: 'sm' | 'md' | 'lg';
  initialFocus?: RefObject<HTMLElement | null>;
}

const widths = { sm: 'max-w-sm', md: 'max-w-lg', lg: 'max-w-2xl' } as const;

/**
 * Centered dialog for short, blocking decisions (confirm, small forms).
 * For object details and longer forms use <SidePanel>.
 * Focus is trapped, Escape closes, the page behind is inert, and focus
 * returns to the trigger.
 */
export function Modal({ open, onClose, title, description, children, footer, size = 'md', initialFocus }: ModalProps) {
  const titleId = useId();
  const descId = useId();
  const ref = useRef<HTMLDivElement>(null);
  const isClient = useIsClient();
  useModalBehavior(open, ref, onClose, { initialFocus });

  if (!isClient) return null;
  return createPortal(
    <AnimatePresence>
      {open && (
        <div className="fixed inset-0 z-panel flex items-center justify-center p-4">
          <motion.div
            className="absolute inset-0 bg-scrim"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            onClick={onClose}
            aria-hidden="true"
          />
          <motion.div
            ref={ref}
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            aria-describedby={description ? descId : undefined}
            tabIndex={-1}
            className={cn(
              'relative flex max-h-[calc(100vh-32px)] w-full flex-col rounded-ng-lg border border-border-2 bg-surface shadow-overlay outline-none',
              widths[size],
            )}
            initial={{ opacity: 0, y: 8, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.98 }}
            transition={{ duration: 0.16, ease: [0.2, 0.7, 0.2, 1] }}
          >
            <div className="flex items-start gap-3 border-b border-border px-5 py-4">
              <div className="min-w-0 flex-1">
                <h2 id={titleId} className="font-display text-lead font-semibold tracking-[-0.025em] text-fg">{title}</h2>
                {description && <p id={descId} className="mt-1 text-ui text-fg-2">{description}</p>}
              </div>
              <button
                type="button"
                onClick={onClose}
                aria-label="Close dialog"
                className="-mr-1 grid h-8 w-8 shrink-0 place-items-center rounded-ctl text-fg-2 hover:bg-surface-2 hover:text-fg"
              >
                <X size={18} aria-hidden="true" />
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">{children}</div>
            {footer && <div className="flex flex-wrap justify-end gap-2 border-t border-border px-5 py-3.5">{footer}</div>}
          </motion.div>
        </div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
