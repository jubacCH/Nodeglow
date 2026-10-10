'use client';

import { useEffect } from 'react';
import { CommandPalette } from './CommandPalette';
import { useUiStore } from '@/stores/ui';

/**
 * Mounts the command palette once (AppShell) and binds Ctrl/Cmd+K. The top
 * bar search button opens it through the same store.
 */
export function CommandPaletteHost() {
  const open = useUiStore((s) => s.paletteOpen);
  const setOpen = useUiStore((s) => s.setPaletteOpen);
  const toggle = useUiStore((s) => s.togglePalette);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        toggle();
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [toggle]);

  return <CommandPalette open={open} onOpenChange={setOpen} />;
}
