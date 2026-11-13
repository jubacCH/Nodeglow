/** Two-factor sign-in of the signed-in user's own account (/api/account/mfa). */
export interface MfaStatus {
  enabled: boolean;
  enabled_at: string | null;
  recovery_codes_left: number;
}

/** The setup key in groups of four, the way authenticator apps show it. */
export function formatSecret(secret: string): string {
  return (secret.replace(/\s+/g, '').match(/.{1,4}/g) ?? []).join(' ');
}
