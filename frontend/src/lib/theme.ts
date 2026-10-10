/**
 * Theme mechanism. The active theme lives in `data-theme` on <html>
 * ("dark" | "light"); every design token switches on it (tokens.gen.css).
 *
 * - THEME_INIT_SCRIPT runs inline in <head> (app/layout.tsx) before first
 *   paint, so a stored light theme never flashes dark.
 * - <ThemeController> keeps the attribute in sync with the theme store and,
 *   for "system", with prefers-color-scheme.
 */
import { useSyncExternalStore } from 'react';

export type ColorMode = 'dark' | 'light' | 'system';
export type ResolvedTheme = 'dark' | 'light';

/** localStorage key of the persisted zustand theme store (stores/theme.ts). */
export const THEME_STORAGE_KEY = 'ng-theme';
export const DEFAULT_FONT_SIZE = 14;

export function resolveColorMode(mode: unknown, prefersLight: boolean): ResolvedTheme {
  if (mode === 'light' || mode === 'dark') return mode;
  if (mode === 'system') return prefersLight ? 'light' : 'dark';
  return 'dark';
}

export function clampFontSize(size: unknown): number {
  const n = Number(size);
  return Number.isFinite(n) && n >= 10 && n <= 24 ? n : DEFAULT_FONT_SIZE;
}

/** Apply a resolved theme and font size to <html>. */
export function applyTheme(theme: ResolvedTheme, fontSize: number, root: HTMLElement = document.documentElement) {
  if (root.getAttribute('data-theme') !== theme) root.setAttribute('data-theme', theme);
  root.style.colorScheme = theme;
  root.style.fontSize = `${fontSize}px`;
}

/**
 * Inline, dependency-free copy of resolveColorMode + applyTheme for <head>.
 * Keep in sync with the functions above (covered by lib/theme.test.ts).
 */
export const THEME_INIT_SCRIPT = `(function(){var d=document.documentElement;try{var s=(JSON.parse(localStorage.getItem('${THEME_STORAGE_KEY}')||'{}')||{}).state||{};var m=s.colorMode;if(m!=='light'&&m!=='dark'){m=(m==='system'&&window.matchMedia&&window.matchMedia('(prefers-color-scheme: light)').matches)?'light':'dark'}var f=Number(s.fontSize);if(!(f>=10&&f<=24))f=${DEFAULT_FONT_SIZE};d.setAttribute('data-theme',m);d.style.colorScheme=m;d.style.fontSize=f+'px'}catch(e){d.setAttribute('data-theme','dark')}})();`;

// ── Reading the active theme in components ───────────────────────────────

function subscribe(cb: () => void) {
  const obs = new MutationObserver(cb);
  obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  return () => obs.disconnect();
}

function getSnapshot(): ResolvedTheme {
  return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
}

/**
 * The theme that is actually applied right now. Re-renders when it changes,
 * whatever changed it (toggle, system preference, another tab). Use it for
 * canvas/JS consumers such as ECharts that cannot follow CSS variables.
 */
export function useResolvedTheme(): ResolvedTheme {
  return useSyncExternalStore(subscribe, getSnapshot, () => 'dark');
}
