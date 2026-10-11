import { create } from 'zustand';
import type { TenantAccess, TenantVia, User } from '@/types';
import { get } from '@/lib/api';

interface MeResponse {
  user: User | null;
  /** The tenant this session works in (absent from older backends). */
  tenant?: { id: number; via: TenantVia };
  /** Every tenant the user may switch into. */
  tenants?: TenantAccess[];
}

interface AuthState {
  user: User | null;
  tenant: { id: number; via: TenantVia } | null;
  tenants: TenantAccess[];
  isLoading: boolean;
  fetchUser: () => Promise<void>;
  logout: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  tenant: null,
  tenants: [],
  isLoading: true,

  fetchUser: async () => {
    try {
      const data = await get<MeResponse>('/api/auth/me');
      set({ user: data.user, tenant: data.tenant ?? null, tenants: data.tenants ?? [], isLoading: false });
    } catch {
      set({ user: null, tenant: null, tenants: [], isLoading: false });
    }
  },

  logout: async () => {
    set({ user: null, tenant: null, tenants: [] });
    await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' }).catch(() => {});
    window.location.href = '/login';
  },
}));

/** Convenience selectors */
export const useUser = () => useAuthStore((s) => s.user);
export const useIsAdmin = () => useAuthStore((s) => s.user?.role === 'admin');
export const useIsEditor = () =>
  useAuthStore((s) => s.user?.role === 'admin' || s.user?.role === 'editor');
export const useIsOperator = () => useAuthStore((s) => s.user?.is_operator === true);
export const useTenants = () => useAuthStore((s) => s.tenants);
export const useActiveTenant = () => useAuthStore((s) => s.tenant);
