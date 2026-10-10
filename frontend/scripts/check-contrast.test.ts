// Prints the contrast table for docs/design/04-design-system.md.
// Run: npx vitest run scripts/check-contrast.test.ts
import { it } from 'vitest';
import { checkContrast } from '../src/styles/contrast';

it('prints the contrast table', () => {
  const rows = checkContrast();
  const lines = ['| Paar | Minimum | Dunkel | Hell |', '|---|---|---|---|'];
  const half = rows.length / 2;
  for (let i = 0; i < half; i++) {
    const d = rows[i];
    const l = rows[i + half];
    const f = (r: typeof d) => `${r.ratio.toFixed(2)}${r.pass ? '' : ' ✗'}`;
    lines.push(`| \`${d.fg}\` auf \`${d.bg}\` | ${d.min} | ${f(d)} | ${f(l)} |`);
  }
  console.log(lines.join('\n'));
  const failed = rows.filter((r) => !r.pass);
  if (failed.length) throw new Error(`${failed.length} pair(s) below minimum:\n${lines.join('\n')}`);
});
