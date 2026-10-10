import { describe, expect, it } from 'vitest';
import { aiUnavailableMessage, describeDestination, type AiStatus } from './ai';

const status = (over: Partial<AiStatus>): AiStatus => ({
  enabled: false, configured: false, available: false,
  provider: 'anthropic', provider_label: 'Anthropic (Claude)', redaction: true,
  ...over,
});

describe('describeDestination', () => {
  it('names the provider and host the data goes to', () => {
    expect(describeDestination('anthropic', '', '')).toContain('api.anthropic.com');
    expect(describeDestination('openai_compatible', 'http://10.0.0.5:11434/v1', ''))
      .toBe('the OpenAI-compatible endpoint at 10.0.0.5');
    expect(describeDestination('openai_compatible', 'https://ng-chn.openai.azure.com', '2024-10-21'))
      .toBe('Azure OpenAI at ng-chn.openai.azure.com');
  });

  it('copes with a half-typed URL', () => {
    expect(describeDestination('openai_compatible', 'ollama.lan', '')).toBe('the OpenAI-compatible endpoint at ollama.lan');
    expect(describeDestination('openai_compatible', '', '')).toBe('the OpenAI-compatible endpoint');
  });
});

describe('aiUnavailableMessage', () => {
  it('tells admins where to enable AI and others whom to ask', () => {
    expect(aiUnavailableMessage(status({}), true)).toMatch(/Enable them under Settings → AI/);
    expect(aiUnavailableMessage(status({}), false)).toMatch(/An admin can enable/);
  });

  it('distinguishes "enabled but not configured"', () => {
    expect(aiUnavailableMessage(status({ enabled: true }), true)).toMatch(/not configured/);
  });
});
