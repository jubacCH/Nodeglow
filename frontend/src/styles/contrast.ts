/**
 * WCAG contrast helpers for the design tokens. Used by the token test suite
 * (tokens.test.ts) and by `npm run check:contrast` to verify the text pairs
 * listed in design-tokens/tokens.json -> contrast.pairs in both themes.
 *
 * Supports the value forms used for colour tokens: #rgb/#rrggbb, rgb()/rgba(),
 * `{ref}` references and `color-mix(in srgb, X P%, transparent)` (composited
 * over the pair's background, which is how a soft tint renders on a card).
 */
import tokens from '../../design-tokens/tokens.json';

export type Rgba = [number, number, number, number];
type ThemeName = 'dark' | 'light';
type Dict = Record<string, string>;

function parseColor(v: string): Rgba | null {
  const s = v.trim();
  const hex = s.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
  if (hex) {
    const h = hex[1].length === 3 ? hex[1].split('').map((c) => c + c).join('') : hex[1];
    return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)).concat(1) as Rgba;
  }
  const rgb = s.match(/^rgba?\(([^)]+)\)$/i);
  if (rgb) {
    const p = rgb[1].split(',').map((x) => parseFloat(x));
    return [p[0], p[1], p[2], p[3] ?? 1];
  }
  if (s === 'transparent') return [0, 0, 0, 0];
  return null;
}

/** Composite `fg` over an opaque `bg`. */
export function over(fg: Rgba, bg: Rgba): Rgba {
  const a = fg[3];
  return [0, 1, 2].map((i) => fg[i] * a + bg[i] * (1 - a)).concat(1) as Rgba;
}

function luminance([r, g, b]: Rgba): number {
  const lin = (c: number) => {
    const v = c / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

export function contrastRatio(a: Rgba, b: Rgba): number {
  const x = luminance(a);
  const y = luminance(b);
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

function dict(theme: ThemeName): Dict {
  return { ...(tokens.shared as Dict), ...(tokens.theme[theme] as Dict) };
}

/** Resolve a token to an RGBA colour (alpha may be < 1). */
export function resolveToken(name: string, theme: ThemeName, depth = 0): Rgba {
  if (depth > 8) throw new Error(`token reference loop at ${name}`);
  const d = dict(theme);
  const raw = d[name];
  if (raw === undefined) throw new Error(`unknown token ${name}`);
  return resolveValue(raw, theme, depth);
}

function resolveValue(raw: string, theme: ThemeName, depth: number): Rgba {
  const d = dict(theme);
  const ref = raw.match(/^\{([a-z0-9-]+)\}$/);
  if (ref) return resolveToken(ref[1], theme, depth + 1);
  const mix = raw.match(/^color-mix\(in srgb,\s*(\{[a-z0-9-]+\}|#[0-9a-f]+)\s+(\{[a-z0-9-]+\}|[\d.]+%),\s*transparent\)$/i);
  if (mix) {
    const base = resolveValue(mix[1], theme, depth + 1);
    const pctRaw = mix[2].startsWith('{') ? d[mix[2].slice(1, -1)] : mix[2];
    const pct = parseFloat(pctRaw) / 100;
    return [base[0], base[1], base[2], base[3] * pct];
  }
  const c = parseColor(raw);
  if (!c) throw new Error(`cannot resolve colour value "${raw}"`);
  return c;
}

export interface ContrastResult {
  theme: ThemeName;
  fg: string;
  bg: string;
  min: number;
  ratio: number;
  pass: boolean;
}

/** Evaluate every pair in tokens.json -> contrast.pairs for both themes.
 *  Translucent backgrounds (soft tints) are composited over `surface`. */
export function checkContrast(): ContrastResult[] {
  const out: ContrastResult[] = [];
  for (const theme of ['dark', 'light'] as const) {
    const surface = resolveToken('surface', theme);
    for (const [fg, bg, min] of tokens.contrast.pairs as [string, string, number][]) {
      const bgc = over(resolveToken(bg, theme), surface);
      const fgc = over(resolveToken(fg, theme), bgc);
      const ratio = contrastRatio(fgc, bgc);
      out.push({ theme, fg, bg, min, ratio: Math.round(ratio * 100) / 100, pass: ratio >= min });
    }
  }
  return out;
}
