import localFont from 'next/font/local';

/*
 * One family for everything: Figtree for UI, body, titles and big numbers.
 * There is no monospace face; addresses, rule names and logs use Figtree with
 * tabular figures (the `font-mono` token points at the same stack).
 *
 * Self-hosted from the @fontsource-variable package (files in node_modules),
 * so `next build` needs no network (next/font/google failed intermittently in
 * CI and cannot work in air-gapped builds).
 *
 * The family is split into a latin and a latin-ext face with the same unicode
 * ranges Google Fonts uses, so a page only downloads latin-ext when it shows
 * such characters. next/font needs literal arguments, hence the repetition.
 * The CSS variables feed --ng-font-* in tokens.gen.css:
 *   --font-figtree      latin face only (no fallback, so the stack can
 *                       continue with the latin-ext face)
 *   --font-figtree-ext  latin-ext face + metric-adjusted fallback
 */

export const figtree = localFont({
  src: '../../node_modules/@fontsource-variable/figtree/files/figtree-latin-wght-normal.woff2',
  weight: '300 900',
  style: 'normal',
  display: 'swap',
  variable: '--font-figtree',
  adjustFontFallback: false,
  declarations: [
    {
      prop: 'unicode-range',
      value:
        'U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD',
    },
  ],
});

export const figtreeExt = localFont({
  src: '../../node_modules/@fontsource-variable/figtree/files/figtree-latin-ext-wght-normal.woff2',
  weight: '300 900',
  style: 'normal',
  display: 'swap',
  variable: '--font-figtree-ext',
  preload: false,
  declarations: [
    {
      prop: 'unicode-range',
      value:
        'U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF',
    },
  ],
});

/** Class names that define all font CSS variables; put them on <html>. */
export const fontVariables = [figtree, figtreeExt].map((f) => f.variable).join(' ');
