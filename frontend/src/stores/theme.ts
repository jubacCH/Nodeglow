import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface ThemeState {
  accentColor: string;
  colorMode: 'dark' | 'light';
  density: 'comfortable' | 'compact';
  fontSize: number;
  sidebarPosition: 'left' | 'right';
  sidebarCollapsed: boolean;
  setAccentColor: (c: string) => void;
  setColorMode: (m: 'dark' | 'light') => void;
  toggleColorMode: () => void;
  setDensity: (d: 'comfortable' | 'compact') => void;
  setFontSize: (s: number) => void;
  setSidebarPosition: (p: 'left' | 'right') => void;
  toggleSidebar: () => void;
}

export const useThemeStore = create<ThemeState>()(
  persist(
    (set) => ({
      accentColor: '#38BDF8',
      colorMode: 'dark',
      density: 'comfortable',
      fontSize: 14,
      sidebarPosition: 'left',
      sidebarCollapsed: false,
      setAccentColor: (accentColor) => set({ accentColor }),
      setColorMode: (colorMode) => set({ colorMode }),
      toggleColorMode: () => set((s) => ({ colorMode: s.colorMode === 'dark' ? 'light' : 'dark' })),
      setDensity: (density) => set({ density }),
      setFontSize: (fontSize) => set({ fontSize }),
      setSidebarPosition: (sidebarPosition) => set({ sidebarPosition }),
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
    }),
    { name: 'ng-theme' },
  ),
);
