'use client';

import { useToastStore, type ToastType } from '@/stores/toast';
import { motion, AnimatePresence } from 'framer-motion';
import { X, CheckCircle2, AlertTriangle, AlertCircle, Info } from 'lucide-react';

const icons = { success: CheckCircle2, error: AlertCircle, warning: AlertTriangle, info: Info };

const iconTone: Record<ToastType, string> = {
  success: 'text-ok',
  error: 'text-down',
  warning: 'text-warning',
  info: 'text-accent',
};

/**
 * Toasts: dark "tip" surface in both themes (E3), bottom centre, above the
 * mobile tab bar. Errors are announced assertively, the rest politely.
 */
export function ToastContainer() {
  const { toasts, dismiss } = useToastStore();

  return (
    <div
      className="pointer-events-none fixed inset-x-0 bottom-6 z-toast flex flex-col items-center gap-2 px-4 max-[759px]:bottom-[76px]"
      aria-live="polite"
      aria-relevant="additions"
    >
      <AnimatePresence>
        {toasts.map((t) => {
          const Icon = icons[t.type];
          return (
            <motion.div
              key={t.id}
              role={t.type === 'error' ? 'alert' : 'status'}
              // Toasts are always a dark surface; scoping the dark theme keeps
              // the status icon colours legible on it in light mode too.
              data-theme="dark"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 8 }}
              transition={{ duration: 0.15 }}
              className="pointer-events-auto flex max-w-[min(560px,100%)] items-center gap-3 rounded-[9px] bg-tip px-3.5 py-2.5 text-ui text-tip-fg shadow-overlay"
            >
              <Icon size={16} className={iconTone[t.type]} aria-hidden="true" />
              <span className="min-w-0 flex-1">{t.message}</span>
              <button
                type="button"
                onClick={() => dismiss(t.id)}
                aria-label="Dismiss notification"
                className="-mr-1 grid h-6 w-6 place-items-center rounded-chip text-tip-fg/70 hover:text-tip-fg"
              >
                <X size={14} aria-hidden="true" />
              </button>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}
