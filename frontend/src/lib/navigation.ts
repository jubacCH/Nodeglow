/**
 * Central navigation registry (IA: docs/design/02-information-architecture.md).
 *
 * One source for the rail, the section sub-navigation, the mobile tab bar,
 * the command palette and the `g …` keyboard shortcuts. Routes keep their
 * current URLs for now; the IA's target URLs come with the redirects later.
 *
 * Every route under src/app/(app) must resolve to an item here
 * (navigation.test.ts enforces this).
 */
import {
  Activity, ArrowUpDown, BarChart3, Bell, BookOpen, Bot, ClipboardList, FileText, History,
  KeyRound, LayoutDashboard, LayoutList, Network, Plug, Radio, Scan, ScrollText,
  Server, Settings, Shield, ShieldCheck, TriangleAlert, Users, Wrench, Archive,
  type LucideIcon,
} from 'lucide-react';

export type NavBadge = 'incidents' | 'discovery';

export interface NavItem {
  id: string;
  label: string;
  href: string;
  icon: LucideIcon;
  /** Extra path prefixes that belong to this item (detail pages). */
  match?: string[];
  /** Query parameters the URL must carry for this item to be active. */
  query?: Record<string, string>;
  adminOnly?: boolean;
  badge?: NavBadge;
  /** `g <key>` shortcut. */
  shortcut?: string;
  /** Extra words for the command palette. */
  keywords?: string;
}

export interface NavSection {
  id: 'overview' | 'incidents' | 'infrastructure' | 'observability' | 'analytics' | 'admin';
  label: string;
  /** Label for the mobile tab bar. */
  shortLabel: string;
  icon: LucideIcon;
  items: NavItem[];
  /** Rail placement: main group or pinned to the bottom. */
  placement: 'main' | 'bottom';
  /** Shown directly in the mobile tab bar (otherwise under "More"). */
  mobile: boolean;
  badge?: NavBadge;
}

export const NAV_SECTIONS: NavSection[] = [
  {
    id: 'overview',
    label: 'Overview',
    shortLabel: 'Overview',
    icon: LayoutDashboard,
    placement: 'main',
    mobile: true,
    items: [
      { id: 'dashboard', label: 'Overview', href: '/', icon: LayoutDashboard, shortcut: 'd', keywords: 'dashboard home' },
      { id: 'changes', label: 'Changes', href: '/changes', icon: History, keywords: 'since last visit feed activity what changed' },
    ],
  },
  {
    id: 'incidents',
    label: 'Incidents & Alerting',
    shortLabel: 'Incidents',
    icon: TriangleAlert,
    placement: 'main',
    mobile: true,
    badge: 'incidents',
    items: [
      { id: 'incidents', label: 'Incidents', href: '/alerts', icon: TriangleAlert, match: ['/incidents'], badge: 'incidents', shortcut: 'n', keywords: 'alerts' },
      { id: 'maintenance', label: 'Maintenance', href: '/alerts?tab=maintenance', icon: Wrench, query: { tab: 'maintenance' }, keywords: 'maintenance windows' },
      { id: 'rules', label: 'Alert rules', href: '/rules', icon: Bell, shortcut: 'r', keywords: 'rules alerting' },
    ],
  },
  {
    id: 'infrastructure',
    label: 'Infrastructure',
    shortLabel: 'Infra',
    icon: Server,
    placement: 'main',
    mobile: true,
    items: [
      { id: 'hosts', label: 'Hosts', href: '/hosts', icon: Server, shortcut: 'h' },
      { id: 'topology', label: 'Topology', href: '/topology', icon: Network, shortcut: 'o', keywords: 'map' },
      { id: 'agents', label: 'Agents & Probes', href: '/agents', icon: Bot, keywords: 'agent probe' },
      { id: 'integrations', label: 'Integrations', href: '/integration/store', icon: Plug, match: ['/integration'], keywords: 'catalog store proxmox unifi' },
      { id: 'snmp', label: 'SNMP', href: '/snmp', icon: Radio, keywords: 'mib oid' },
      { id: 'discovery', label: 'Discovery', href: '/tasks', icon: ClipboardList, badge: 'discovery', keywords: 'tasks inbox ports' },
      { id: 'scanner', label: 'Scans', href: '/scanner', icon: Scan, keywords: 'scanner network scan' },
      { id: 'certificates', label: 'Certificates', href: '/ssl', icon: ShieldCheck, keywords: 'ssl tls' },
      { id: 'backups', label: 'Backups', href: '/backups', icon: Archive },
    ],
  },
  {
    id: 'observability',
    label: 'Observability',
    shortLabel: 'Logs',
    icon: ScrollText,
    placement: 'main',
    mobile: true,
    items: [
      { id: 'logs', label: 'Logs', href: '/syslog', icon: ScrollText, shortcut: 'l', keywords: 'syslog explorer' },
      { id: 'logs-overview', label: 'Log overview', href: '/syslog/dashboard', icon: LayoutList, keywords: 'syslog dashboard' },
      { id: 'log-patterns', label: 'Log patterns', href: '/syslog/templates', icon: FileText, keywords: 'templates' },
      { id: 'traffic', label: 'Traffic', href: '/bandwidth', icon: ArrowUpDown, keywords: 'bandwidth' },
    ],
  },
  {
    id: 'analytics',
    label: 'Analytics',
    shortLabel: 'Analytics',
    icon: BarChart3,
    placement: 'main',
    mobile: true,
    items: [
      { id: 'reports', label: 'Reports', href: '/digest', icon: BookOpen, keywords: 'digest weekly' },
    ],
  },
  {
    id: 'admin',
    label: 'Administration',
    shortLabel: 'Admin',
    icon: Settings,
    placement: 'bottom',
    mobile: false,
    items: [
      { id: 'settings', label: 'Settings', href: '/settings', icon: Settings, adminOnly: true, shortcut: 'i' },
      { id: 'users', label: 'Users', href: '/users', icon: Users, adminOnly: true },
      { id: 'credentials', label: 'Credentials', href: '/credentials', icon: KeyRound },
      { id: 'system', label: 'System status', href: '/system/status', icon: Activity, shortcut: 't', keywords: 'updates version' },
      { id: 'audit', label: 'Audit log', href: '/system/audit', icon: Shield, adminOnly: true },
    ],
  },
];

/** Sections and items the user may see. Empty sections are dropped. */
export function visibleSections(isAdmin: boolean): NavSection[] {
  return NAV_SECTIONS
    .map((s) => ({ ...s, items: s.items.filter((i) => isAdmin || !i.adminOnly) }))
    .filter((s) => s.items.length > 0);
}

function pathOf(href: string) {
  return href.split('?')[0];
}

function pathMatches(pathname: string, prefix: string) {
  if (prefix === '/') return pathname === '/';
  return pathname === prefix || pathname.startsWith(prefix + '/');
}

export interface ActiveNav {
  section: NavSection;
  item: NavItem;
}

/**
 * The section and item for a URL. Among items whose path matches, the one
 * with the most matching query parameters wins, then the longest path.
 */
export function findActive(
  pathname: string,
  search: URLSearchParams | null = null,
  sections: NavSection[] = NAV_SECTIONS,
): ActiveNav | null {
  let best: (ActiveNav & { score: number }) | null = null;
  for (const section of sections) {
    for (const item of section.items) {
      const prefixes = [pathOf(item.href), ...(item.match ?? [])];
      const hit = prefixes.filter((p) => pathMatches(pathname, p)).sort((a, b) => b.length - a.length)[0];
      if (hit === undefined) continue;
      const q = item.query ?? {};
      const qKeys = Object.keys(q);
      if (qKeys.some((k) => search?.get(k) !== q[k])) continue;
      const score = qKeys.length * 1000 + hit.length;
      if (!best || score > best.score) best = { section, item, score };
    }
  }
  return best ? { section: best.section, item: best.item } : null;
}

/** Shortcut key -> href, from the items that declare one. */
export function shortcutRoutes(): Record<string, { href: string; label: string }> {
  const out: Record<string, { href: string; label: string }> = {};
  for (const s of NAV_SECTIONS) for (const i of s.items) if (i.shortcut) out[i.shortcut] = { href: i.href, label: i.label };
  // Kept from the old sidebar: g a (alerts) and g s (syslog).
  out.a ??= { href: '/alerts', label: 'Incidents' };
  out.s ??= { href: '/syslog', label: 'Logs' };
  return out;
}
