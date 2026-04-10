'use client';

import { useEffect, useState } from 'react';
import { CommandPalette } from './CommandPalette';

/**
 * Mounts the command palette once at the layout level and binds the global
 * Cmd/Ctrl+K shortcut. Place this once in app/(app)/layout.tsx.
 */
export function CommandPaletteHost() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, []);

  return <CommandPalette open={open} onOpenChange={setOpen} />;
}
