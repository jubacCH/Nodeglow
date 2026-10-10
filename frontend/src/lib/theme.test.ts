import { describe, expect, it } from 'vitest';
import { THEME_INIT_SCRIPT, clampFontSize, resolveColorMode } from './theme';

/** Run the inline <head> script against a fake document/localStorage. */
function runInitScript(stored: unknown, prefersLight = false) {
  const attrs: Record<string, string> = {};
  const style: Record<string, string> = {};
  const documentElement = {
    setAttribute: (k: string, v: string) => { attrs[k] = v; },
    style,
  };
  const localStorage = {
    getItem: () => (stored === undefined ? null : JSON.stringify(stored)),
  };
  const window = { matchMedia: () => ({ matches: prefersLight }) };
  new Function('document', 'localStorage', 'window', THEME_INIT_SCRIPT)(
    { documentElement }, localStorage, window,
  );
  return { theme: attrs['data-theme'], fontSize: style.fontSize, colorScheme: style.colorScheme };
}

describe('resolveColorMode', () => {
  it('defaults to dark', () => {
    expect(resolveColorMode(undefined, true)).toBe('dark');
    expect(resolveColorMode('nonsense', true)).toBe('dark');
  });
  it('follows the OS only for "system"', () => {
    expect(resolveColorMode('system', true)).toBe('light');
    expect(resolveColorMode('system', false)).toBe('dark');
    expect(resolveColorMode('dark', true)).toBe('dark');
    expect(resolveColorMode('light', false)).toBe('light');
  });
});

describe('clampFontSize', () => {
  it('keeps sane values and falls back to 14', () => {
    expect(clampFontSize(16)).toBe(16);
    expect(clampFontSize(3)).toBe(14);
    expect(clampFontSize('x')).toBe(14);
  });
});

describe('THEME_INIT_SCRIPT', () => {
  it('applies dark without a stored theme', () => {
    expect(runInitScript(undefined)).toEqual({ theme: 'dark', fontSize: '14px', colorScheme: 'dark' });
  });
  it('applies a stored light theme and font size before paint', () => {
    expect(runInitScript({ state: { colorMode: 'light', fontSize: 16 } })).toEqual({
      theme: 'light', fontSize: '16px', colorScheme: 'light',
    });
  });
  it('resolves "system" with prefers-color-scheme', () => {
    expect(runInitScript({ state: { colorMode: 'system' } }, true).theme).toBe('light');
    expect(runInitScript({ state: { colorMode: 'system' } }, false).theme).toBe('dark');
  });
});
