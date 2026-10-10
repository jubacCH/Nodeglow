import { describe, expect, it } from 'vitest';
import { hasAnyAiFeature, hasFeature, lacksFeature, type Features } from './features';

const community: Features = {
  edition: 'community',
  features: { ha_scheduler: false, ai_assistant: false, ai_postmortem: false, ai_daily_summary: false },
};
const enterprise: Features = {
  edition: 'enterprise',
  features: { ha_scheduler: true, ai_assistant: true, ai_postmortem: true, ai_daily_summary: true },
};

describe('feature flags', () => {
  it('reports an installed feature only when the flag is on', () => {
    expect(hasFeature(enterprise, 'ai_assistant')).toBe(true);
    expect(hasFeature(community, 'ai_assistant')).toBe(false);
  });

  it('treats unknown flags (loading, error) as neither present nor absent', () => {
    expect(hasFeature(undefined, 'ai_postmortem')).toBe(false);
    expect(lacksFeature(undefined, 'ai_postmortem')).toBe(false);
  });

  it('lacksFeature is true for a known-off or missing flag', () => {
    expect(lacksFeature(community, 'ai_postmortem')).toBe(true);
    expect(lacksFeature({ edition: 'community', features: {} }, 'ai_postmortem')).toBe(true);
    expect(lacksFeature(enterprise, 'ai_postmortem')).toBe(false);
  });

  it('hasAnyAiFeature looks at the AI flags only', () => {
    expect(hasAnyAiFeature(community)).toBe(false);
    expect(hasAnyAiFeature({ edition: 'community', features: { ha_scheduler: true } })).toBe(false);
    expect(hasAnyAiFeature({ edition: 'enterprise', features: { ai_daily_summary: true } })).toBe(true);
  });
});
