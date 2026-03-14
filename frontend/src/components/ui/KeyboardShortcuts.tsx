'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import { Modal } from '@/components/ui/Modal';

const kbd = 'bg-white/[0.08] border border-white/[0.12] rounded px-1.5 py-0.5 text-xs font-mono text-slate-300';

function Kbd({ children }: { children: string }) {
  return <kbd className={kbd}>{children}</kbd>;
}

interface Shortcut {
  keys: string[];
  description: string;
}

interface ShortcutGroup {
  label: string;
  shortcuts: Shortcut[];
}

const groups: ShortcutGroup[] = [
  {
    label: 'Navigation',
    shortcuts: [
      { keys: ['g', 'd'], description: 'Go to Dashboard' },
      { keys: ['g', 'h'], description: 'Go to Hosts' },
      { keys: ['g', 'a'], description: 'Go to Alerts' },
      { keys: ['g', 's'], description: 'Go to Syslog' },
      { keys: ['g', 'r'], description: 'Go to Rules' },
      { keys: ['g', 'i'], description: 'Go to Settings' },
      { keys: ['g', 't'], description: 'Go to Status' },
    ],
  },
  {
    label: 'Global',
    shortcuts: [
      { keys: ['⌘', 'K'], description: 'Search' },
      { keys: ['?'], description: 'This help' },
    ],
  },
];

export function KeyboardShortcuts() {
  const [open, setOpen] = useState(false);
  const pendingG = useRef(false);
  const gTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const isInputFocused = useCallback(() => {
    const el = document.activeElement;
    if (!el) return false;
    const tag = el.tagName.toLowerCase();
    return tag === 'input' || tag === 'textarea' || tag === 'select' || (el as HTMLElement).isContentEditable;
  }, []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (isInputFocused()) return;

      // "?" to open help
      if (e.key === '?' && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        setOpen(true);
        return;
      }

      // g + <key> navigation sequences
      if (e.key === 'g' && !e.ctrlKey && !e.metaKey && !e.altKey) {
        pendingG.current = true;
        if (gTimer.current) clearTimeout(gTimer.current);
        gTimer.current = setTimeout(() => { pendingG.current = false; }, 800);
        return;
      }

      if (pendingG.current) {
        pendingG.current = false;
        if (gTimer.current) clearTimeout(gTimer.current);

        const routes: Record<string, string> = {
          d: '/',
          h: '/hosts',
          a: '/alerts',
          s: '/syslog',
          r: '/rules',
          i: '/settings',
          t: '/status',
        };

        const path = routes[e.key];
        if (path) {
          e.preventDefault();
          window.location.href = path;
        }
      }
    };

    document.addEventListener('keydown', handler);
    return () => {
      document.removeEventListener('keydown', handler);
      if (gTimer.current) clearTimeout(gTimer.current);
    };
  }, [isInputFocused]);

  return (
    <Modal open={open} onClose={() => setOpen(false)} title="Keyboard Shortcuts">
      <div className="space-y-6">
        {groups.map((group) => (
          <div key={group.label}>
            <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-3">
              {group.label}
            </h4>
            <div className="space-y-2">
              {group.shortcuts.map((shortcut) => (
                <div
                  key={shortcut.description}
                  className="flex items-center justify-between py-1"
                >
                  <span className="text-sm text-slate-300">{shortcut.description}</span>
                  <div className="flex items-center gap-1">
                    {shortcut.keys.map((key, i) => (
                      <span key={i} className="flex items-center gap-1">
                        {i > 0 && <span className="text-slate-500 text-xs">+</span>}
                        <Kbd>{key}</Kbd>
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </Modal>
  );
}
