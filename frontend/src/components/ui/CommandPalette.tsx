'use client';

import { Command } from 'cmdk';
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useHostSearch } from '@/hooks/queries/useDashboard';
import { useIntegrations } from '@/hooks/queries/useIntegrations';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import { useIsAdmin } from '@/stores/auth';
import { useThemeStore } from '@/stores/theme';
import { useUiStore } from '@/stores/ui';
import { visibleSections } from '@/lib/navigation';
import { Keyboard, Moon, Plug, RefreshCw, Search, Server } from 'lucide-react';
import { StatusDot } from './StatusDot';
import { Kbd } from './Kbd';

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Global search (Ctrl/Cmd+K). Searches pages from the navigation registry,
 * hosts (server-side, from 2 characters) and integration instances; plus a
 * few actions. Data is fetched only while the palette is open.
 */
export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const router = useRouter();
  const isAdmin = useIsAdmin();
  const toggleColorMode = useThemeStore((s) => s.toggleColorMode);
  const setShortcutsOpen = useUiStore((s) => s.setShortcutsOpen);
  const [search, setSearch] = useState('');
  const debouncedSearch = useDebouncedValue(search, 200);
  const { data: hosts, isFetching: hostsLoading } = useHostSearch(debouncedSearch, open);
  const { data: integrations } = useIntegrations(undefined, { enabled: open });
  const sections = visibleSections(isAdmin);

  useEffect(() => {
    if (!open) setSearch('');
  }, [open]);

  const go = (href: string) => {
    onOpenChange(false);
    router.push(href);
  };
  const run = (fn: () => void) => {
    onOpenChange(false);
    fn();
  };

  return (
    <Command.Dialog
      open={open}
      onOpenChange={onOpenChange}
      label="Search"
      className="cmdk-dialog"
      shouldFilter
    >
      <div className="cmdk-shell">
        <div className="cmdk-input-wrap">
          <Search size={18} aria-hidden="true" />
          <Command.Input
            value={search}
            onValueChange={setSearch}
            placeholder="Search pages, hosts, integrations…"
            className="cmdk-input"
          />
          <Kbd>Esc</Kbd>
        </div>
        <Command.List className="cmdk-list">
          {hostsLoading && <Command.Loading>Searching hosts…</Command.Loading>}
          <Command.Empty className="cmdk-empty">No results. Try a host name, IP or page.</Command.Empty>

          {sections.map((section) => (
            <Command.Group key={section.id} heading={section.label} className="cmdk-group">
              {section.items.map((item) => (
                <Command.Item
                  key={item.id}
                  value={`${section.label} ${item.label} ${item.keywords ?? ''} ${item.href}`}
                  onSelect={() => go(item.href)}
                  className="cmdk-item"
                >
                  <item.icon size={16} aria-hidden="true" />
                  <span>{item.label}</span>
                  {item.shortcut && <span className="cmdk-meta">g {item.shortcut}</span>}
                </Command.Item>
              ))}
            </Command.Group>
          ))}

          {debouncedSearch.trim().length >= 2 && (hosts?.length ?? 0) > 0 && (
            <Command.Group heading="Hosts" className="cmdk-group">
              {hosts!.map((h) => {
                const name = h.name || h.hostname;
                const status = h.online === false ? 'down' : h.online === true ? 'ok' : 'unknown';
                return (
                  <Command.Item
                    key={h.id}
                    value={`host ${name} ${h.hostname} ${h.id}`}
                    onSelect={() => go(`/hosts/${h.id}`)}
                    className="cmdk-item"
                  >
                    <span className="grid place-items-center"><Server size={16} aria-hidden="true" /></span>
                    <span className="flex min-w-0 items-center gap-2">
                      <StatusDot status={status} size="sm" />
                      <span className="truncate">{name}</span>
                    </span>
                    <span className="cmdk-meta font-mono">{h.hostname}</span>
                  </Command.Item>
                );
              })}
            </Command.Group>
          )}

          {(integrations?.length ?? 0) > 0 && (
            <Command.Group heading="Integrations" className="cmdk-group">
              {integrations!.map((ih) => (
                <Command.Item
                  key={`${ih.type}-${ih.id}`}
                  value={`integration ${ih.name} ${ih.type} ${ih.id}`}
                  onSelect={() => go(`/integration/${ih.type}/${ih.id}`)}
                  className="cmdk-item"
                >
                  <Plug size={16} aria-hidden="true" />
                  <span>{ih.name}</span>
                  <span className="cmdk-meta">{ih.type}</span>
                </Command.Item>
              ))}
            </Command.Group>
          )}

          <Command.Group heading="Actions" className="cmdk-group">
            <Command.Item value="action theme dark light toggle" onSelect={() => run(toggleColorMode)} className="cmdk-item">
              <Moon size={16} aria-hidden="true" />
              <span>Switch theme</span>
            </Command.Item>
            <Command.Item value="action keyboard shortcuts help" onSelect={() => run(() => setShortcutsOpen(true))} className="cmdk-item">
              <Keyboard size={16} aria-hidden="true" />
              <span>Keyboard shortcuts</span>
              <span className="cmdk-meta">?</span>
            </Command.Item>
            <Command.Item value="action reload refresh" onSelect={() => run(() => window.location.reload())} className="cmdk-item">
              <RefreshCw size={16} aria-hidden="true" />
              <span>Reload page</span>
            </Command.Item>
          </Command.Group>
        </Command.List>
        <div className="cmdk-footer" aria-hidden="true">
          <span><Kbd>↑</Kbd> <Kbd>↓</Kbd> to move</span>
          <span><Kbd>Enter</Kbd> to open</span>
          <span><Kbd>Esc</Kbd> to close</span>
        </div>
      </div>
    </Command.Dialog>
  );
}
