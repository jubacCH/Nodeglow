/**
 * Multi-tenancy in the UI (ee, phase 9): the tenant switcher, the operator's
 * Tenants page and support access. Pure helpers (no React) plus the switch
 * itself; the backend decides everything, this only presents it.
 */
import type { QueryClient } from '@tanstack/react-query';
import { post } from './api';
import type { TenantAccess, TenantVia } from '@/types';

/** The switcher is shown only when there is something to switch to. */
export function showTenantSwitcher(tenants: readonly TenantAccess[] | undefined): boolean {
  return (tenants?.length ?? 0) > 1;
}

/** Two letters for the tenant badge ("Acme AG" → "AA", "beta" → "BE"). */
export function tenantInitials(name: string): string {
  const words = name.trim().split(/[\s._-]+/).filter(Boolean);
  if (words.length === 0) return '?';
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

/** How the access was granted, for people (null: a plain membership). */
export function viaLabel(via: TenantVia | undefined | null): string | null {
  switch (via) {
    case undefined:
    case null:
    case 'membership':
      return null;
    case 'support':
      return 'Support access';
    case 'provider_role':
      return 'Provider role';
    case 'operator':
      return 'Operator';
    default:
      return String(via);
  }
}

/**
 * Switch the session to another tenant, then drop every cached query so
 * nothing of the previous tenant stays on screen (resetQueries clears the
 * data and refetches what is mounted) and re-read /api/auth/me.
 */
export async function switchTenant(
  tenantId: number,
  queryClient: QueryClient,
  refreshUser: () => Promise<void>,
): Promise<void> {
  await post('/api/auth/tenant', { tenant_id: tenantId });
  await refreshUser();
  await queryClient.resetQueries();
}

// ── Tenants page (operator) ──────────────────────────────────────────────────

export type TenantStatus = 'active' | 'suspended' | 'deleting';

export interface TenantRow {
  id: number;
  name: string;
  slug: string;
  provider_id: number;
  status: TenantStatus;
  plan: string | null;
  plan_name: string | null;
  retention_days: number | null;
  created_at: string | null;
  suspended_at: string | null;
  delete_after: string | null;
  deletion_requested_at: string | null;
  purge_started_at: string | null;
  members: number | null;
  default: boolean;
}

export interface PlanInfo {
  key: string;
  name: string;
  retention_days: number;
}

export interface TenantsPayload {
  tenants: TenantRow[];
  usage: { count: number; max: number | null; licensed: boolean; licence?: { status: string; message: string } };
  plans: PlanInfo[];
  default_plan: string;
  creation_problems: string[];
  deletion_grace_days: number;
}

/** "3 / 10 tenants" or "3 tenants · no limit". */
export function usageLabel(usage: TenantsPayload['usage'] | undefined): string {
  if (!usage) return '';
  const n = `${usage.count} tenant${usage.count === 1 ? '' : 's'}`;
  return usage.max == null ? `${n} · no licence limit` : `${usage.count} / ${usage.max} tenants (licence)`;
}

/** The status vocabulary of lib/status.ts for a tenant state. */
export function tenantStatusTone(status: TenantStatus): 'ok' | 'maint' | 'down' {
  if (status === 'active') return 'ok';
  if (status === 'suspended') return 'maint';
  return 'down';
}

export function tenantStatusLabel(row: Pick<TenantRow, 'status' | 'purge_started_at'>): string {
  if (row.status === 'deleting') return row.purge_started_at ? 'Purging' : 'Deletion scheduled';
  return row.status === 'active' ? 'Active' : 'Suspended';
}

/** When a deletion requested now would be purged. */
export function purgeDate(now: Date, graceDays: number): Date {
  return new Date(now.getTime() + graceDays * 86_400_000);
}

/** What a plan change would lower: retention key -> stored and new days (409 retention_would_lower). */
export type RetentionLowering = Record<string, { from: number; to: number }>;

const RETENTION_LABELS: Record<string, string> = {
  ping_retention_days: 'Ping history',
  proxmox_retention_days: 'Proxmox history',
  integration_retention_days: 'Integration history',
  incident_event_retention_days: 'Incident events',
  template_retention_days: 'Log templates',
  template_singleton_retention_days: 'One-off log templates',
};

/** One line per lowered setting, e.g. "Incident events: keep forever → 30 days". */
export function retentionLoweringLines(lowered: RetentionLowering): string[] {
  return Object.entries(lowered).map(([key, { from, to }]) => {
    const label = RETENTION_LABELS[key] ?? key;
    const before = from <= 0 ? 'keep forever' : `${from} days`;
    return `${label}: ${before} → ${to} days`;
  });
}

/** The plan to send for a select value: '' is "no plan" (null). */
export function planFromSelect(value: string): string | null {
  return value === '' ? null : value;
}

/** The typed confirmation must be the tenant's name, exactly (spaces around ignored). */
export function deletionConfirmed(typed: string, name: string): boolean {
  return typed.trim() === name && name.length > 0;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

// ── Support access ───────────────────────────────────────────────────────────

export interface SupportGrant {
  id: number;
  role: 'admin' | 'editor' | 'readonly';
  reason: string;
  created_at: string | null;
  expires_at: string | null;
  granted_by: string | null;
  revoked_at: string | null;
  revoked_by: string | null;
  status: 'active' | 'expired' | 'revoked';
}

export interface SupportState {
  active: SupportGrant[];
  history: SupportGrant[];
  can_manage: boolean;
  can_grant: boolean;
  via_support: boolean;
  limits: { min_hours: number; max_hours: number; roles: string[] };
}

/** The banner line while support access is active, or null for no banner. */
export function supportBanner(state: SupportState | undefined): string | null {
  if (!state) return null;
  const latest = [...state.active].sort((a, b) => (b.expires_at ?? '').localeCompare(a.expires_at ?? ''))[0];
  if (state.via_support) {
    return `You are working in this tenant through support access${latest ? ` until ${formatDateTime(latest.expires_at)}` : ''}. Every access is recorded in the tenant's audit log.`;
  }
  if (!latest) return null;
  const by = latest.granted_by ? ` (granted by ${latest.granted_by})` : '';
  return `Support access is active until ${formatDateTime(latest.expires_at)}${by}: the operators can see this tenant's data as ${latest.role}.`;
}

/** Duration presets for a grant, within the backend's limits. */
export function durationOptions(limits: SupportState['limits'] | undefined): { hours: number; label: string }[] {
  const all = [
    { hours: 1, label: '1 hour' }, { hours: 4, label: '4 hours' }, { hours: 24, label: '1 day' },
    { hours: 72, label: '3 days' }, { hours: 168, label: '7 days' }, { hours: 336, label: '14 days' },
  ];
  if (!limits) return all;
  return all.filter((o) => o.hours >= limits.min_hours && o.hours <= limits.max_hours);
}
