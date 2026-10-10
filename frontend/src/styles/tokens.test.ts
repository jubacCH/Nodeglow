import { describe, expect, it } from 'vitest';
import tokens from '../../design-tokens/tokens.json';
import { checkContrast } from './contrast';

describe('design tokens', () => {
  it('defines every theme token in both themes', () => {
    expect(Object.keys(tokens.theme.light).sort()).toEqual(Object.keys(tokens.theme.dark).sort());
  });

  it('ships only the Glow Violet accent', () => {
    expect(tokens.theme.dark.accent).toBe('#7C6CFF');
    expect(tokens.theme.light.accent).toBe('#5B4BE0');
  });

  it('turns glow into tinted shadow and stops breathing in light mode', () => {
    expect(tokens.theme.light.breathe).toBe('none');
    expect(tokens.theme.light['wire-glow']).toBe('none');
    expect(tokens.theme.dark.breathe).toMatch(/ng-breathe/);
  });

  it('meets the WCAG contrast minimum for every listed pair in both themes', () => {
    const failures = checkContrast().filter((r) => !r.pass);
    expect(failures).toEqual([]);
  });
});
