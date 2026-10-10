/**
 * Edition and feature flags from GET /api/v2/features (pure helpers, no React).
 *
 * The UI is the same in every edition (it is AGPL, like the core); it only
 * shows what the backend reports as installed. A flag says a feature exists in
 * this installation — whether it is switched on (e.g. the AI opt-in) is a
 * separate question answered by that feature's own status.
 */

export type Edition = 'community' | 'enterprise';

export type FeatureName = 'ha_scheduler' | 'ai_assistant' | 'ai_postmortem' | 'ai_daily_summary';

export interface Features {
  edition: Edition;
  features: Partial<Record<FeatureName, boolean>> & Record<string, boolean>;
}

/** True only when the flag is known and on. Unknown (still loading, error) is false. */
export function hasFeature(data: Features | undefined, name: FeatureName): boolean {
  return data?.features?.[name] === true;
}

/** True only when the flags are known and this one is off — the case for the enterprise note. */
export function lacksFeature(data: Features | undefined, name: FeatureName): boolean {
  return !!data && data.features?.[name] !== true;
}

export const AI_FEATURES: readonly FeatureName[] = ['ai_assistant', 'ai_postmortem', 'ai_daily_summary'];

/** Whether any user-facing AI feature is installed. */
export function hasAnyAiFeature(data: Features | undefined): boolean {
  return AI_FEATURES.some((f) => hasFeature(data, f));
}

/** The one sentence shown where an enterprise feature would be. */
export const ENTERPRISE_NOTES = {
  ai: 'Glow, AI postmortems and the AI daily summary are part of Nodeglow Enterprise.',
  ai_postmortem: 'AI postmortem drafts are part of Nodeglow Enterprise.',
} as const;
