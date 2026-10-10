import { describe, expect, it } from 'vitest';
import { cn } from './utils';

describe('cn (tailwind-merge with the design-system scale)', () => {
  it('keeps a size class next to a colour class', () => {
    expect(cn('text-meta', 'text-fg-2')).toBe('text-meta text-fg-2');
    expect(cn('text-ui text-on-accent')).toBe('text-ui text-on-accent');
  });
  it('still lets a later size win over an earlier one', () => {
    expect(cn('text-meta', 'text-h1')).toBe('text-h1');
    expect(cn('text-fg', 'text-down')).toBe('text-down');
  });
  it('merges custom shadows and radii', () => {
    expect(cn('shadow-glow-warn', 'shadow-glow-crit')).toBe('shadow-glow-crit');
    expect(cn('rounded-ctl', 'rounded-pill')).toBe('rounded-pill');
  });
});
