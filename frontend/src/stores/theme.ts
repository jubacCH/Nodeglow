import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { THEME_STORAGE_KEY, resolveColorMode, type ColorMode } from '@/lib/theme';

interface ThemeState {
  /** User choice. "system" follows prefers-color-scheme. Default: dark. */
  colorMode: ColorMode;
  density: 'comfortable' | 'compact';
  fontSize: number;
  sidebarCollapsed: boolean;
  setColorMode: (m: ColorMode) => void;
  /** Switch between dark and light (leaves "system"). */
  toggleColorMode: () => void;
  setDensity: (d: 'comfortable' | 'compact') => void;
  setFontSize: (s: number) => void;
  toggleSidebar: () => void;
}

function prefersLight() {
  return typeof window !== 'undefined' && !!window.matchMedia
    && window.matchMedia('(prefers-color-scheme: light)').matches;
}

export const useThemeStore = create<ThemeState>()(
  persist(
    (set) => ({
      colorMode: 'dark',
      density: 'comfortable',
      fontSize: 14,
      sidebarCollapsed: false,
      setColorMode: (colorMode) => set({ colorMode }),
      toggleColorMode: () =>
        set((s) => ({
          colorMode: resolveColorMode(s.colorMode, prefersLight()) === 'dark' ? 'light' : 'dark',
        })),
      setDensity: (density) => set({ density }),
      setFontSize: (fontSize) => set({ fontSize }),
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
    }),
    { name: THEME_STORAGE_KEY },
  ),
);
