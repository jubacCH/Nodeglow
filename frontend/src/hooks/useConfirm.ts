'use client';

import { useState, useCallback, useRef, createElement } from 'react';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';

interface ConfirmOptions {
  title: string;
  description: string;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: 'default' | 'danger';
}

export function useConfirm() {
  const [state, setState] = useState<ConfirmOptions | null>(null);
  const resolveRef = useRef<((value: boolean) => void) | null>(null);

  const confirm = useCallback((options: ConfirmOptions): Promise<boolean> => {
    setState(options);
    return new Promise<boolean>((resolve) => {
      resolveRef.current = resolve;
    });
  }, []);

  const handleClose = useCallback(() => {
    setState(null);
    resolveRef.current?.(false);
    resolveRef.current = null;
  }, []);

  const handleConfirm = useCallback(() => {
    setState(null);
    resolveRef.current?.(true);
    resolveRef.current = null;
  }, []);

  const ConfirmDialogElement = createElement(ConfirmDialog, {
    open: state !== null,
    onClose: handleClose,
    onConfirm: handleConfirm,
    title: state?.title ?? '',
    description: state?.description ?? '',
    confirmLabel: state?.confirmLabel,
    cancelLabel: state?.cancelLabel,
    variant: state?.variant,
  });

  return { confirm, ConfirmDialogElement } as const;
}
