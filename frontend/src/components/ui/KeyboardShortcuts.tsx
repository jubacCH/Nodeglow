'use client';

import { useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { Modal } from '@/components/ui/Modal';
import { Kbd } from '@/components/ui/Kbd';
import { shortcutRoutes } from '@/lib/navigation';
import { useUiStore } from '@/stores/ui';

function isTyping() {
  const el = document.activeElement as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName.toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || el.isContentEditable;
}

/**
 * Global keyboard shortcuts: `g` + key navigates (targets come from the
 * navigation registry, lib/navigation.ts), `?` opens this help.
 */
export function KeyboardShortcuts() {
  const router = useRouter();
  const open = useUiStore((s) => s.shortcutsOpen);
  const setOpen = useUiStore((s) => s.setShortcutsOpen);
  const pendingG = useRef(false);
  const gTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const routes = shortcutRoutes();

  useEffect(() => {
    const table = shortcutRoutes();
    const handler = (e: KeyboardEvent) => {
      if (isTyping() || e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === '?') {
        e.preventDefault();
        setOpen(true);
        return;
      }
      if (e.key === 'g' && !pendingG.current) {
        pendingG.current = true;
        if (gTimer.current) clearTimeout(gTimer.current);
        gTimer.current = setTimeout(() => { pendingG.current = false; }, 800);
        return;
      }
      if (pendingG.current) {
        pendingG.current = false;
        if (gTimer.current) clearTimeout(gTimer.current);
        const target = table[e.key];
        if (target) {
          e.preventDefault();
          router.push(target.href);
        }
      }
    };
    document.addEventListener('keydown', handler);
    return () => {
      document.removeEventListener('keydown', handler);
      if (gTimer.current) clearTimeout(gTimer.current);
    };
  }, [router, setOpen]);

  const nav = Object.entries(routes).sort(([a], [b]) => a.localeCompare(b));

  return (
    <Modal open={open} onClose={() => setOpen(false)} title="Keyboard shortcuts">
      <div className="space-y-6">
        <section>
          <h3 className="mb-2 text-meta font-medium text-fg-2">Navigation</h3>
          <ul className="divide-y divide-border">
            {nav.map(([key, r]) => (
              <li key={key} className="flex items-center justify-between py-2">
                <span className="text-ui text-fg">Go to {r.label}</span>
                <span className="flex items-center gap-1"><Kbd>g</Kbd><Kbd>{key}</Kbd></span>
              </li>
            ))}
          </ul>
        </section>
        <section>
          <h3 className="mb-2 text-meta font-medium text-fg-2">Global</h3>
          <ul className="divide-y divide-border">
            <li className="flex items-center justify-between py-2">
              <span className="text-ui text-fg">Search</span>
              <span className="flex items-center gap-1"><Kbd>Ctrl</Kbd><Kbd>K</Kbd></span>
            </li>
            <li className="flex items-center justify-between py-2">
              <span className="text-ui text-fg">This help</span>
              <Kbd>?</Kbd>
            </li>
          </ul>
        </section>
      </div>
    </Modal>
  );
}
