'use client';

import { Command } from 'cmdk';
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useDashboard, useNavCounts } from '@/hooks/queries/useDashboard';
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
 * - Hosts (dynamically pulled from /api/dashboard)
 * - Integration instances (dynamically pulled from /api/dashboard)
 * - Quick actions (refresh, theme toggle later, etc.)
 */
export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const router = useRouter();
  const [search, setSearch] = useState('');
  const { data: dashData } = useDashboard();
  useNavCounts(); // keeps badge counts warm so navigation stays fresh

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

          {(dashData?.host_stats?.length ?? 0) > 0 && (
            <Command.Group heading="Hosts" className="cmdk-group">
              {dashData!.host_stats!.slice(0, 50).map((hs) => {
                const id = hs.host.id;
                const name = hs.host.name || hs.host.hostname;
                const online = hs.online;
                return (
                  <Command.Item
                    key={id}
                    value={`host ${name} ${hs.host.hostname}`}
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
                    <span className="cmdk-meta">{hs.host.hostname}</span>
                  </Command.Item>
                );
              })}
            </Command.Group>
          )}

          {(dashData?.integration_health?.length ?? 0) > 0 && (
            <Command.Group heading="Integrations" className="cmdk-group">
              {dashData!.integration_health!.map((ih, i) => {
                const href = ih.single_instance
                  ? `/integration/${ih.type}`
                  : `/integration/${ih.type}/${ih.config_id}`;
                return (
                  <Command.Item
                    key={`${ih.type}-${ih.config_id ?? i}`}
                    value={`integration ${ih.name} ${ih.label} ${ih.type}`}
                    onSelect={() => go(href)}
                    className="cmdk-item"
                  >
                    <Plug size={14} className="text-violet-400" />
                    <span>{ih.name}</span>
                    <span className="cmdk-meta">{ih.label}</span>
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
