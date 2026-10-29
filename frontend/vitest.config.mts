import { defineConfig } from 'vitest/config';

// tsconfig keeps `jsx: preserve` for Next.js; tests compile JSX themselves.
export default defineConfig({
  oxc: { jsx: { runtime: 'automatic' } },
  resolve: { alias: { '@': `${import.meta.dirname}/src` } },
});
