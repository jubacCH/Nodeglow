/** Pure helpers for the AI opt-in UI (kept free of React/API imports for tests). */

/** Whether AI features (Glow, postmortems, daily summary) may be used. */
export interface AiStatus {
  enabled: boolean;
  configured: boolean;
  /** enabled && configured */
  available: boolean;
  provider: string;
  provider_label: string;
  redaction: boolean;
}

export type AiProvider = 'anthropic' | 'openai_compatible';

/** Shown wherever an AI feature is unavailable. */
export function aiUnavailableMessage(status: AiStatus | undefined, isAdmin: boolean): string {
  if (status?.enabled && !status.configured) {
    return isAdmin
      ? 'AI is enabled but the provider is not configured. Finish the setup under Settings → AI.'
      : 'AI is enabled but not configured yet. Ask an admin to finish the setup under Settings → AI.';
  }
  return isAdmin
    ? 'AI features are off. Enable them under Settings → AI (opt-in; you choose the provider and what is redacted).'
    : 'AI features are off for this installation. An admin can enable them under Settings → AI.';
}

/** Where the data would go with the values currently in the form. */
export function describeDestination(provider: AiProvider, baseUrl: string, apiVersion: string): string {
  if (provider === 'anthropic') return 'Anthropic (api.anthropic.com, USA)';
  let host = baseUrl.trim();
  try {
    host = new URL(baseUrl.trim()).hostname || host;
  } catch {
    /* not a full URL yet */
  }
  const kind = apiVersion.trim() ? 'Azure OpenAI' : 'the OpenAI-compatible endpoint';
  return host ? `${kind} at ${host}` : kind;
}
