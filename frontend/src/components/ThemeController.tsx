'use client';

import { useEffect } from 'react';
import { useThemeStore } from '@/stores/theme';
import { applyTheme, clampFontSize, resolveColorMode } from '@/lib/theme';

/**
 * Keeps `data-theme` and the root font size on <html> in sync with the theme
 * store. The first paint is handled by THEME_INIT_SCRIPT in app/layout.tsx;
 * this takes over after hydration (toggle, "system" changes, other tabs).
 * Renders nothing. Mounted once in <Providers>.
 */
export function ThemeController() {
  const colorMode = useThemeStore((s) => s.colorMode);
  const fontSize = useThemeStore((s) => s.fontSize);

  useEffect(() => {
    const mq = window.matchMedia?.('(prefers-color-scheme: light)');
    const apply = () => applyTheme(resolveColorMode(colorMode, !!mq?.matches), clampFontSize(fontSize));
    apply();
    if (colorMode !== 'system' || !mq) return;
    mq.addEventListener('change', apply);
    return () => mq.removeEventListener('change', apply);
  }, [colorMode, fontSize]);

  // Another tab changed the theme: pick up the persisted value.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === useThemeStore.persist.getOptions().name) void useThemeStore.persist.rehydrate();
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  return null;
}
