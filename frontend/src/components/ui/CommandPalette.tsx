'use client';

import { Command } from 'cmdk';
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useHostSearch } from '@/hooks/queries/useDashboard';
import { useIntegrations } from '@/hooks/queries/useIntegrations';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import {
  LayoutDashboard, Server, AlertTriangle, Bell, FileText, Bot, Scan,
  Radio, ShieldCheck, KeyRound, ClipboardList, Network, ArrowUpDown,
  Activity, Shield, BookOpen, Settings, Users, Plug, RefreshCw,
  Search,
} from 'lucide-react';

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

interface NavEntry {
  label: string;
  href: string;
  icon: React.ElementType;
  group: 'Navigation' | 'System';
}

const NAV_ENTRIES: NavEntry[] = [
  { label: 'Dashboard',      href: '/',               icon: LayoutDashboard, group: 'Navigation' },
  { label: 'Hosts',          href: '/hosts',          icon: Server,          group: 'Navigation' },
  { label: 'Alerts',         href: '/alerts',         icon: AlertTriangle,   group: 'Navigation' },
  { label: 'Rules',          href: '/rules',          icon: Bell,            group: 'Navigation' },
  { label: 'Syslog',         href: '/syslog',         icon: FileText,        group: 'Navigation' },
  { label: 'Agents',         href: '/agents',         icon: Bot,             group: 'Navigation' },
  { label: 'Scanner',        href: '/scanner',        icon: Scan,            group: 'Navigation' },
  { label: 'SNMP',           href: '/snmp',           icon: Radio,           group: 'Navigation' },
  { label: 'SSL',            href: '/ssl',            icon: ShieldCheck,     group: 'Navigation' },
  { label: 'Credentials',    href: '/credentials',    icon: KeyRound,        group: 'Navigation' },
  { label: 'Tasks',          href: '/tasks',          icon: ClipboardList,   group: 'Navigation' },
  { label: 'Topology',       href: '/topology',       icon: Network,         group: 'Navigation' },
  { label: 'Bandwidth',      href: '/bandwidth',      icon: ArrowUpDown,     group: 'Navigation' },
  { label: 'Integrations',   href: '/integration/store', icon: Plug,         group: 'Navigation' },
  { label: 'System Status',  href: '/system/status',  icon: Activity,        group: 'System' },
  { label: 'Audit Log',      href: '/system/audit',   icon: Shield,          group: 'System' },
  { label: 'Digest',         href: '/digest',         icon: BookOpen,        group: 'System' },
  { label: 'Settings',       href: '/settings',       icon: Settings,        group: 'System' },
  { label: 'Users',          href: '/users',          icon: Users,           group: 'System' },
];

/**
 * Global command palette — Cmd/Ctrl+K to open, ESC to close.
 *
 * Searches across:
 * - Navigation entries (always)
 * - Hosts (server-side search, /hosts/api/search, once 2+ chars are typed)
 * - Integration instances (/api/v1/integrations)
 *
 * Both are fetched only while the palette is open.
 * - Quick actions (refresh, theme toggle later, etc.)
 */
export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const router = useRouter();
  const [search, setSearch] = useState('');
  const debouncedSearch = useDebouncedValue(search, 200);
  const { data: hosts, isFetching: hostsLoading } = useHostSearch(debouncedSearch, open);
  const { data: integrations } = useIntegrations(undefined, { enabled: open });

  // Reset search when the palette is closed
  useEffect(() => {
    if (!open) setSearch('');
  }, [open]);

  // Convenience navigation closure
  const go = (href: string) => {
    onOpenChange(false);
    router.push(href);
  };

  return (
    <Command.Dialog
      open={open}
      onOpenChange={onOpenChange}
      label="Command palette"
      className="cmdk-dialog"
      shouldFilter
    >
      <div className="cmdk-shell">
        <div className="cmdk-input-wrap">
          <Search size={16} className="text-slate-500" />
          <Command.Input
            value={search}
            onValueChange={setSearch}
            placeholder="Type a command, host name, or integration…"
            className="cmdk-input"
            autoFocus
          />
          <kbd className="cmdk-kbd">ESC</kbd>
        </div>
        <Command.List className="cmdk-list">
          {hostsLoading && <Command.Loading>Searching hosts…</Command.Loading>}
          <Command.Empty className="cmdk-empty">
            No results. Try a different search term.
          </Command.Empty>

          <Command.Group heading="Navigation" className="cmdk-group">
            {NAV_ENTRIES.filter((n) => n.group === 'Navigation').map((n) => (
              <Command.Item
                key={n.href}
                value={`nav ${n.label} ${n.href}`}
                onSelect={() => go(n.href)}
                className="cmdk-item"
              >
                <n.icon size={14} className="text-slate-400" />
                <span>{n.label}</span>
              </Command.Item>
            ))}
          </Command.Group>

          <Command.Group heading="System" className="cmdk-group">
            {NAV_ENTRIES.filter((n) => n.group === 'System').map((n) => (
              <Command.Item
                key={n.href}
                value={`sys ${n.label} ${n.href}`}
                onSelect={() => go(n.href)}
                className="cmdk-item"
              >
                <n.icon size={14} className="text-slate-400" />
                <span>{n.label}</span>
              </Command.Item>
            ))}
          </Command.Group>

          {debouncedSearch.trim().length >= 2 && (hosts?.length ?? 0) > 0 && (
            <Command.Group heading="Hosts" className="cmdk-group">
              {hosts!.map((h) => {
                const id = h.id;
                const name = h.name || h.hostname;
                const online = h.online;
                return (
                  <Command.Item
                    key={id}
                    value={`host ${name} ${h.hostname} ${id}`}
                    onSelect={() => go(`/hosts/${id}`)}
                    className="cmdk-item"
                  >
                    <span
                      className={
                        'inline-block w-2 h-2 rounded-full ' +
                        (online === false
                          ? 'bg-red-500'
                          : online === true
                          ? 'bg-emerald-500'
                          : 'bg-slate-500')
                      }
                    />
                    <span>{name}</span>
                    <span className="cmdk-meta">{h.hostname}</span>
                  </Command.Item>
                );
              })}
            </Command.Group>
          )}

          {(integrations?.length ?? 0) > 0 && (
            <Command.Group heading="Integrations" className="cmdk-group">
              {integrations!.map((ih) => {
                const href = `/integration/${ih.type}/${ih.id}`;
                return (
                  <Command.Item
                    key={`${ih.type}-${ih.id}`}
                    value={`integration ${ih.name} ${ih.type} ${ih.id}`}
                    onSelect={() => go(href)}
                    className="cmdk-item"
                  >
                    <Plug size={14} className="text-violet-400" />
                    <span>{ih.name}</span>
                    <span className="cmdk-meta">{ih.type}</span>
                  </Command.Item>
                );
              })}
            </Command.Group>
          )}

          <Command.Group heading="Actions" className="cmdk-group">
            <Command.Item
              value="action refresh"
              onSelect={() => {
                onOpenChange(false);
                window.location.reload();
              }}
              className="cmdk-item"
            >
              <RefreshCw size={14} className="text-slate-400" />
              <span>Reload page</span>
            </Command.Item>
          </Command.Group>
        </Command.List>
      </div>
    </Command.Dialog>
  );
}
