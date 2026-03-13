import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface ThemeState {
  accentColor: string;
  density: 'comfortable' | 'compact';
  fontSize: number;
  sidebarPosition: 'left' | 'right';
  sidebarCollapsed: boolean;
  setAccentColor: (c: string) => void;
  setDensity: (d: 'comfortable' | 'compact') => void;
  setFontSize: (s: number) => void;
  setSidebarPosition: (p: 'left' | 'right') => void;
  toggleSidebar: () => void;
}

export const useThemeStore = create<ThemeState>()(
  persist(
    (set) => ({
      accentColor: '#38BDF8',
      density: 'comfortable',
      fontSize: 14,
      sidebarPosition: 'left',
      sidebarCollapsed: false,
      setAccentColor: (accentColor) => set({ accentColor }),
      setDensity: (density) => set({ density }),
      setFontSize: (fontSize) => set({ fontSize }),
      setSidebarPosition: (sidebarPosition) => set({ sidebarPosition }),
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
    }),
    { name: 'ng-theme' },
  ),
);
