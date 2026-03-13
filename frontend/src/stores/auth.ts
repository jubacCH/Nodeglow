import { create } from 'zustand';
import type { User } from '@/types';
import { get } from '@/lib/api';

interface AuthState {
  user: User | null;
  isLoading: boolean;
  fetchUser: () => Promise<void>;
  logout: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  isLoading: true,

  fetchUser: async () => {
    try {
      const data = await get<{ user: User | null }>('/api/auth/me');
      set({ user: data.user, isLoading: false });
    } catch {
      set({ user: null, isLoading: false });
    }
  },

  logout: () => {
    set({ user: null });
    window.location.href = '/logout';
  },
}));

/** Convenience selectors */
export const useUser = () => useAuthStore((s) => s.user);
export const useIsAdmin = () => useAuthStore((s) => s.user?.role === 'admin');
export const useIsEditor = () =>
  useAuthStore((s) => s.user?.role === 'admin' || s.user?.role === 'editor');
