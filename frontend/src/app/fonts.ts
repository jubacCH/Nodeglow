import localFont from 'next/font/local';

/*
 * Fonts per concept E3: Sora for display text and big numbers, Inter Tight
 * for UI and body, JetBrains Mono for rule names, logs and addresses.
 *
 * Self-hosted from the @fontsource-variable packages (files in node_modules),
 * so `next build` needs no network (next/font/google failed intermittently in
 * CI and cannot work in air-gapped builds).
 *
 * Each family is split into a latin and a latin-ext face with the same
 * unicode ranges Google Fonts uses, so a page only downloads latin-ext when it
 * shows such characters. next/font needs literal arguments, hence the
 * repetition. The CSS variables feed --ng-font-* in tokens.gen.css:
 *   --font-inter-tight      latin face only (no fallback, so the stack can
 *                           continue with the latin-ext face)
 *   --font-inter-tight-ext  latin-ext face + metric-adjusted fallback
 * and the same for --font-sora(-ext) and --font-jetbrains-mono(-ext).
 */

export const interTight = localFont({
  src: '../../node_modules/@fontsource-variable/inter-tight/files/inter-tight-latin-wght-normal.woff2',
  weight: '100 900',
  style: 'normal',
  display: 'swap',
  variable: '--font-inter-tight',
  adjustFontFallback: false,
  declarations: [
    {
      prop: 'unicode-range',
      value:
        'U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD',
    },
  ],
});

export const interTightExt = localFont({
  src: '../../node_modules/@fontsource-variable/inter-tight/files/inter-tight-latin-ext-wght-normal.woff2',
  weight: '100 900',
  style: 'normal',
  display: 'swap',
  variable: '--font-inter-tight-ext',
  preload: false,
  declarations: [
    {
      prop: 'unicode-range',
      value:
        'U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF',
    },
  ],
});

export const sora = localFont({
  src: '../../node_modules/@fontsource-variable/sora/files/sora-latin-wght-normal.woff2',
  weight: '100 800',
  style: 'normal',
  display: 'swap',
  variable: '--font-sora',
  adjustFontFallback: false,
  declarations: [
    {
      prop: 'unicode-range',
      value:
        'U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD',
    },
  ],
});

export const soraExt = localFont({
  src: '../../node_modules/@fontsource-variable/sora/files/sora-latin-ext-wght-normal.woff2',
  weight: '100 800',
  style: 'normal',
  display: 'swap',
  variable: '--font-sora-ext',
  preload: false,
  declarations: [
    {
      prop: 'unicode-range',
      value:
        'U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF',
    },
  ],
});

export const jetbrainsMono = localFont({
  src: '../../node_modules/@fontsource-variable/jetbrains-mono/files/jetbrains-mono-latin-wght-normal.woff2',
  weight: '100 800',
  style: 'normal',
  display: 'swap',
  variable: '--font-jetbrains-mono',
  adjustFontFallback: false,
  declarations: [
    {
      prop: 'unicode-range',
      value:
        'U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD',
    },
  ],
});

export const jetbrainsMonoExt = localFont({
  src: '../../node_modules/@fontsource-variable/jetbrains-mono/files/jetbrains-mono-latin-ext-wght-normal.woff2',
  weight: '100 800',
  style: 'normal',
  display: 'swap',
  variable: '--font-jetbrains-mono-ext',
  preload: false,
  // A monospace font has no sensible Arial-based metric fallback.
  adjustFontFallback: false,
  declarations: [
    {
      prop: 'unicode-range',
      value:
        'U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF',
    },
  ],
});

/** Class names that define all font CSS variables; put them on <html>. */
export const fontVariables = [interTight, interTightExt, sora, soraExt, jetbrainsMono, jetbrainsMonoExt]
  .map((f) => f.variable)
  .join(' ');
