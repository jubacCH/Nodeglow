import type { Config } from 'tailwindcss';
import { ngColors, ngFont, ngRadius } from './src/styles/tokens.gen';

const config: Config = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ngFont.sans.split(','),
        mono: ngFont.mono.split(','),
      },
      colors: {
        // Generated from design-tokens/tokens.json — do not hardcode here.
        // Run `npm run build:tokens` after editing tokens.json.
        ng: ngColors,
      },
      borderRadius: {
        'ng-card': ngRadius.card,
        'ng-button': ngRadius.button,
        'ng-pill': ngRadius.pill,
      },
    },
  },
  plugins: [],
};
export default config;
