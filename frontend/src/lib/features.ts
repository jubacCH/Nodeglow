/**
 * Edition and feature flags from GET /api/v2/features (pure helpers, no React).
 *
 * The UI is the same in every edition (it is AGPL, like the core); it only
 * shows what the backend reports as usable. A flag in `features` says a
 * feature exists in this installation and, in the enterprise edition, is
 * covered by the license; `installed` tells "not in this edition" apart from
 * "not licensed". Whether a feature is switched on (e.g. the AI opt-in) is a
 * separate question answered by that feature's own status.
 */

export type Edition = 'community' | 'enterprise';

export type FeatureName = 'ha_scheduler' | 'ai_assistant' | 'ai_postmortem' | 'ai_daily_summary';

export type LicenseState = 'missing' | 'invalid' | 'valid' | 'grace' | 'expired' | 'error';

/** License summary for every signed-in user (details: GET /settings/license, admins). */
export interface LicenseSummary {
  status: LicenseState;
  message: string;
  expires_at?: string | null;
  grace_until?: string | null;
  days_left?: number | null;
}

type Flags = Partial<Record<FeatureName, boolean>> & Record<string, boolean>;

export interface Features {
  edition: Edition;
  features: Flags;
  /** Installed in this build, licensed or not (absent from older backends). */
  installed?: Flags;
  /** `null` in the community edition. */
  license?: LicenseSummary | null;
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

const LICENSE_SUBJECT = {
  ai: 'Glow, AI postmortems and the AI daily summary',
  ai_postmortem: 'AI postmortem drafts',
} as const;

/**
 * The note for a missing feature: "part of Enterprise" in the community
 * edition, a license sentence when the feature is installed but not licensed.
 */
export function enterpriseNote(data: Features | undefined, kind: keyof typeof ENTERPRISE_NOTES): string {
  const flags: readonly FeatureName[] = kind === 'ai' ? AI_FEATURES : ['ai_postmortem'];
  const installed = data?.edition === 'enterprise' && flags.some((f) => data.installed?.[f] === true);
  if (!installed) return ENTERPRISE_NOTES[kind];
  const subject = LICENSE_SUBJECT[kind];
  if (data?.license?.status === 'expired') {
    return `${subject} are paused: the Nodeglow Enterprise license has expired. Existing data stays visible.`;
  }
  return `${subject} need a Nodeglow Enterprise license (Settings → License).`;
}

/** What the admin banner in the shell shows, or null for no banner. */
export function licenseBanner(data: Features | undefined): { tone: 'warning' | 'down'; text: string } | null {
  const lic = data?.edition === 'enterprise' ? data.license : null;
  if (!lic) return null;
  if (lic.status === 'grace') return { tone: 'warning', text: lic.message };
  if (lic.status === 'expired' || lic.status === 'invalid') return { tone: 'down', text: lic.message };
  return null;
}
