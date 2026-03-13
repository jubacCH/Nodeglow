import type { Config } from 'tailwindcss';

const config: Config = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['var(--font-inter)', 'system-ui', 'sans-serif'],
        mono: ['var(--font-mono)', 'monospace'],
      },
      colors: {
        ng: {
          bg: '#0B0E14',
          surface: '#111621',
          elevated: '#1A1F2E',
          border: '#1E2433',
          primary: '#38BDF8',
          accent: '#A78BFA',
          success: '#34D399',
          warning: '#FBBF24',
          critical: '#F87171',
        },
      },
    },
  },
  plugins: [],
};
export default config;
