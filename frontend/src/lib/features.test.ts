import { describe, expect, it } from 'vitest';
import {
  ENTERPRISE_NOTES, enterpriseNote, hasAnyAiFeature, hasFeature, lacksFeature, licenseBanner,
  type Features, type LicenseSummary,
} from './features';

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

  it('enterpriseNote tells "not in this edition" apart from "not licensed"', () => {
    expect(enterpriseNote(community, 'ai')).toBe(ENTERPRISE_NOTES.ai);
    const unlicensed: Features = {
      edition: 'enterprise',
      features: { ai_postmortem: false },
      installed: { ai_postmortem: true },
      license: { status: 'missing', message: 'No license installed' },
    };
    expect(enterpriseNote(unlicensed, 'ai_postmortem')).toMatch(/need a Nodeglow Enterprise license/);
    const expired: Features = { ...unlicensed, license: { status: 'expired', message: 'expired' } };
    expect(enterpriseNote(expired, 'ai_postmortem')).toMatch(/has expired.*Existing data stays visible/);
    // Older backend without `installed`: the edition note.
    expect(enterpriseNote({ edition: 'enterprise', features: {} }, 'ai')).toBe(ENTERPRISE_NOTES.ai);
  });

  it('licenseBanner shows only for grace, expired and invalid licenses', () => {
    const withLicense = (status: LicenseSummary['status']): Features => ({
      ...enterprise, license: { status, message: `license ${status}` },
    });
    expect(licenseBanner(community)).toBeNull();
    expect(licenseBanner(undefined)).toBeNull();
    expect(licenseBanner(withLicense('valid'))).toBeNull();
    expect(licenseBanner(withLicense('missing'))).toBeNull();
    expect(licenseBanner(withLicense('grace'))).toEqual({ tone: 'warning', text: 'license grace' });
    expect(licenseBanner(withLicense('expired'))?.tone).toBe('down');
    expect(licenseBanner(withLicense('invalid'))?.tone).toBe('down');
  });

  it('hasAnyAiFeature looks at the AI flags only', () => {
    expect(hasAnyAiFeature(community)).toBe(false);
    expect(hasAnyAiFeature({ edition: 'community', features: { ha_scheduler: true } })).toBe(false);
    expect(hasAnyAiFeature({ edition: 'enterprise', features: { ai_daily_summary: true } })).toBe(true);
  });
});
