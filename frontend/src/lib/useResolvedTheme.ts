'use client';

import { useSyncExternalStore } from 'react';
import type { ResolvedTheme } from './theme';

function subscribe(cb: () => void) {
  const obs = new MutationObserver(cb);
  obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  return () => obs.disconnect();
}

function getSnapshot(): ResolvedTheme {
  return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
}

/**
 * The theme that is actually applied right now (data-theme on <html>).
 * Re-renders when it changes, whatever changed it (toggle, system
 * preference, another tab). Use it for canvas/JS consumers such as ECharts
 * that cannot follow CSS variables.
 */
export function useResolvedTheme(): ResolvedTheme {
  return useSyncExternalStore(subscribe, getSnapshot, () => 'dark');
}
