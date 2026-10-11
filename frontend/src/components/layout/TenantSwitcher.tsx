'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useQueryClient } from '@tanstack/react-query';
import { Building2, Check, ChevronDown } from 'lucide-react';
import { Popover, PopoverItem } from '@/components/ui/Popover';
import { Tag } from '@/components/ui/Tag';
import { apiErrorMessage } from '@/lib/api';
import { showTenantSwitcher, switchTenant, tenantInitials, viaLabel } from '@/lib/tenants';
import { cn } from '@/lib/utils';
import { useAuthStore, useIsOperator } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import { useWsStore } from '@/stores/websocket';
import { hasFeature, useFeatures } from '@/hooks/queries/useFeatures';
import type { TenantAccess } from '@/types';

function TenantBadge({ name, size = 'md' }: { name: string; size?: 'sm' | 'md' }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'grid shrink-0 place-items-center rounded-[6px] bg-surface-3 font-semibold text-fg-2',
        size === 'md' ? 'h-6 w-6 text-micro' : 'h-[22px] w-[22px] text-[10px]',
      )}
    >
      {tenantInitials(name)}
    </span>
  );
}

/**
 * Tenant switcher in the top bar. Hidden when the user can reach one tenant
 * only (every single-tenant install). Lists the user's tenants with role and
 * how access is granted; switching moves the session (POST /api/auth/tenant),
 * drops every cached query and reconnects the live socket, so nothing of the
 * previous tenant stays on screen.
 */
export function TenantSwitcher() {
  const tenants = useAuthStore((s) => s.tenants);
  const active = useAuthStore((s) => s.tenant);
  const fetchUser = useAuthStore((s) => s.fetchUser);
  const isOperator = useIsOperator();
  const { data: features } = useFeatures(isOperator);
  const qc = useQueryClient();
  const connect = useWsStore((s) => s.connect);
  const disconnect = useWsStore((s) => s.disconnect);
  const toast = useToastStore((s) => s.show);
  const [busy, setBusy] = useState<number | null>(null);

  if (!showTenantSwitcher(tenants)) return null;
  const current = tenants.find((t) => t.id === active?.id) ?? null;
  const currentName = current?.name ?? 'Tenant';
  const showManage = isOperator && hasFeature(features, 'multi_tenancy');

  async function choose(t: TenantAccess, close: () => void) {
    if (t.id === active?.id) {
      close();
      return;
    }
    setBusy(t.id);
    try {
      disconnect();
      await switchTenant(t.id, qc, fetchUser);
      toast(`Switched to ${t.name}`, 'success');
      close();
    } catch (err) {
      toast(apiErrorMessage(err, 'Could not switch tenant'), 'error');
    } finally {
      connect(qc);
      setBusy(null);
    }
  }

  return (
    <Popover
      label="Tenant"
      placement="bottom-start"
      className="w-[300px]"
      trigger={(props) => (
        <button
          {...props}
          type="button"
          data-testid="tenant-switcher"
          aria-label={`Tenant: ${currentName}. Switch tenant`}
          className="flex h-[36px] min-w-0 items-center gap-2 rounded-ctl pl-1.5 pr-2.5 hover:bg-surface-2"
        >
          <TenantBadge name={currentName} />
          <span className="truncate text-ui font-medium text-fg max-[420px]:max-w-[110px]">{currentName}</span>
          {viaLabel(current?.via) && (
            <Tag className="max-[759px]:hidden">{viaLabel(current?.via)}</Tag>
          )}
          <ChevronDown size={14} aria-hidden="true" className="shrink-0 text-fg-3" />
        </button>
      )}
    >
      {(close) => (
        <>
          <p className="px-2.5 pb-1.5 pt-1 text-meta text-fg-3">Switch tenant</p>
          <ul className="max-h-[50vh] overflow-y-auto" aria-label="Tenants">
            {tenants.map((t) => {
              const isCurrent = t.id === active?.id;
              const via = viaLabel(t.via);
              return (
                <li key={t.id}>
                  <PopoverItem current={isCurrent} onClick={() => choose(t, close)}>
                    <span className="flex items-center gap-2.5">
                      <TenantBadge name={t.name} size="sm" />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate">{t.name}</span>
                        <span className="block text-meta capitalize text-fg-3">
                          {t.role}{via ? ` · ${via}` : ''}
                        </span>
                      </span>
                      {busy === t.id && <span className="text-meta text-fg-3">Switching…</span>}
                      {isCurrent && <Check size={14} aria-label="Current tenant" className="shrink-0 text-accent" />}
                    </span>
                  </PopoverItem>
                </li>
              );
            })}
          </ul>
          {showManage && (
            <div className="mt-1 border-t border-border pt-1">
              <Link
                href="/tenants"
                onClick={() => close()}
                className="flex items-center gap-2.5 rounded-ng-sm px-2.5 py-[9px] text-ui text-fg hover:bg-surface-2"
              >
                <Building2 size={16} aria-hidden="true" className="text-fg-2" />
                Manage tenants
              </Link>
            </div>
          )}
        </>
      )}
    </Popover>
  );
}
