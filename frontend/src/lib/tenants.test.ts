import { afterEach, describe, expect, it, vi } from 'vitest';
import { QueryClient } from '@tanstack/react-query';
import {
  deletionConfirmed, durationOptions, planFromSelect, purgeDate, retentionLoweringLines,
  showTenantSwitcher, supportBanner, switchTenant, tenantInitials, tenantStatusLabel, tenantStatusTone, usageLabel, viaLabel,
  type SupportState,
} from './tenants';
import type { TenantAccess } from '@/types';

const one: TenantAccess[] = [{ id: 1, name: 'Default', role: 'admin', via: 'membership' }];
const two: TenantAccess[] = [...one, { id: 2, name: 'Acme AG', role: 'readonly', via: 'support' }];

describe('tenant switcher', () => {
  it('is hidden with one tenant (every single-tenant install) and shown with several', () => {
    expect(showTenantSwitcher(undefined)).toBe(false);
    expect(showTenantSwitcher([])).toBe(false);
    expect(showTenantSwitcher(one)).toBe(false);
    expect(showTenantSwitcher(two)).toBe(true);
  });

  it('labels how access is granted; a plain membership needs no label', () => {
    expect(viaLabel('membership')).toBeNull();
    expect(viaLabel(undefined)).toBeNull();
    expect(viaLabel('support')).toBe('Support access');
    expect(viaLabel('provider_role')).toBe('Provider role');
  });

  it('builds two-letter badges', () => {
    expect(tenantInitials('Acme AG')).toBe('AA');
    expect(tenantInitials('beta')).toBe('BE');
    expect(tenantInitials('  ')).toBe('?');
    expect(tenantInitials('north-west_ops')).toBe('NW');
  });
});

describe('switchTenant', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('moves the session, re-reads the user and drops every cached query of the old tenant', async () => {
    const calls: { url: string; body: unknown }[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
      return new Response(JSON.stringify({ ok: true, tenant_id: 2 }), {
        status: 200, headers: { 'content-type': 'application/json' },
      });
    }));
    const qc = new QueryClient();
    qc.setQueryData(['hosts'], [{ name: 'CANARY-OLD-TENANT' }]);
    const refreshUser = vi.fn(async () => {});
    await switchTenant(2, qc, refreshUser);
    expect(calls).toEqual([{ url: '/api/auth/tenant', body: { tenant_id: 2 } }]);
    expect(refreshUser).toHaveBeenCalledOnce();
    // Nothing of the previous tenant is left in the cache.
    expect(qc.getQueryData(['hosts'])).toBeUndefined();
  });

  it('keeps the cache when the switch is refused (404 for a foreign tenant)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"error":"Not found"}', { status: 404 })));
    const qc = new QueryClient();
    qc.setQueryData(['hosts'], [{ name: 'mine' }]);
    const refreshUser = vi.fn(async () => {});
    await expect(switchTenant(3, qc, refreshUser)).rejects.toThrow();
    expect(refreshUser).not.toHaveBeenCalled();
    expect(qc.getQueryData(['hosts'])).toEqual([{ name: 'mine' }]);
  });
});

describe('Tenants page helpers', () => {
  it('shows licence usage x / max', () => {
    expect(usageLabel({ count: 3, max: 10, licensed: true })).toBe('3 / 10 tenants (licence)');
    expect(usageLabel({ count: 1, max: null, licensed: true })).toBe('1 tenant · no licence limit');
  });

  it('maps tenant states to the status vocabulary (suspended is maintenance, never amber)', () => {
    expect(tenantStatusTone('active')).toBe('ok');
    expect(tenantStatusTone('suspended')).toBe('maint');
    expect(tenantStatusTone('deleting')).toBe('down');
    expect(tenantStatusLabel({ status: 'deleting', purge_started_at: null })).toBe('Deletion scheduled');
    expect(tenantStatusLabel({ status: 'deleting', purge_started_at: '2026-10-11T00:00:00Z' })).toBe('Purging');
  });

  it('needs the exact name to confirm a deletion and names the purge date', () => {
    expect(deletionConfirmed('Acme AG', 'Acme AG')).toBe(true);
    expect(deletionConfirmed(' Acme AG ', 'Acme AG')).toBe(true);
    expect(deletionConfirmed('acme ag', 'Acme AG')).toBe(false);
    expect(deletionConfirmed('', '')).toBe(false);
    const now = new Date('2026-10-11T12:00:00Z');
    expect(purgeDate(now, 30).toISOString()).toBe('2026-11-10T12:00:00.000Z');
  });

  it('names what a plan change would lower, "keep forever" included', () => {
    expect(retentionLoweringLines({
      incident_event_retention_days: { from: 0, to: 30 },
      integration_retention_days: { from: 80, to: 30 },
      some_new_key: { from: 60, to: 30 },
    })).toEqual([
      'Incident events: keep forever → 30 days',
      'Integration history: 80 days → 30 days',
      'some_new_key: 60 days → 30 days',
    ]);
  });

  it('sends "no plan" as null', () => {
    expect(planFromSelect('')).toBeNull();
    expect(planFromSelect('standard')).toBe('standard');
  });
});

describe('support access', () => {
  const base: SupportState = {
    active: [], history: [], can_manage: true, can_grant: true, via_support: false,
    limits: { min_hours: 1, max_hours: 336, roles: ['readonly', 'editor', 'admin'] },
  };
  const grant = {
    id: 1, role: 'readonly' as const, reason: 'ticket', created_at: '2026-10-11T10:00:00Z',
    expires_at: '2026-10-12T10:00:00Z', granted_by: 'beta-admin', revoked_at: null, revoked_by: null,
    status: 'active' as const,
  };

  it('shows no banner without an active grant', () => {
    expect(supportBanner(undefined)).toBeNull();
    expect(supportBanner(base)).toBeNull();
  });

  it('tells every member while a grant is active, and the operator working through it', () => {
    expect(supportBanner({ ...base, active: [grant] })).toMatch(/Support access is active until .*beta-admin.*readonly/);
    expect(supportBanner({ ...base, active: [grant], via_support: true })).toMatch(/through support access/);
  });

  it('offers durations within the limits', () => {
    expect(durationOptions({ ...base.limits, max_hours: 24 }).map((o) => o.hours)).toEqual([1, 4, 24]);
    expect(durationOptions(undefined).length).toBe(6);
  });
});
