import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { THEME_STORAGE_KEY, resolveColorMode, type ColorMode } from '@/lib/theme';

interface ThemeState {
  /** @deprecated The accent is fixed to Glow Violet (design tokens). Kept so
   *  persisted state from older versions still parses; it is not applied. */
  accentColor: string;
  /** User choice. "system" follows prefers-color-scheme. Default: dark. */
  colorMode: ColorMode;
  density: 'comfortable' | 'compact';
  fontSize: number;
  /** @deprecated The E3 shell always shows the rail on the left. */
  sidebarPosition: 'left' | 'right';
  sidebarCollapsed: boolean;
  setAccentColor: (c: string) => void;
  setColorMode: (m: ColorMode) => void;
  /** Switch between dark and light (leaves "system"). */
  toggleColorMode: () => void;
  setDensity: (d: 'comfortable' | 'compact') => void;
  setFontSize: (s: number) => void;
  setSidebarPosition: (p: 'left' | 'right') => void;
  toggleSidebar: () => void;
}

function prefersLight() {
  return typeof window !== 'undefined' && !!window.matchMedia
    && window.matchMedia('(prefers-color-scheme: light)').matches;
}

export const useThemeStore = create<ThemeState>()(
  persist(
    (set) => ({
      accentColor: 'violet',
      colorMode: 'dark',
      density: 'comfortable',
      fontSize: 14,
      sidebarPosition: 'left',
      sidebarCollapsed: false,
      setAccentColor: (accentColor) => set({ accentColor }),
      setColorMode: (colorMode) => set({ colorMode }),
      toggleColorMode: () =>
        set((s) => ({
          colorMode: resolveColorMode(s.colorMode, prefersLight()) === 'dark' ? 'light' : 'dark',
        })),
      setDensity: (density) => set({ density }),
      setFontSize: (fontSize) => set({ fontSize }),
      setSidebarPosition: (sidebarPosition) => set({ sidebarPosition }),
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
    }),
    { name: THEME_STORAGE_KEY },
  ),
);
