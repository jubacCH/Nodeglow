/** A deploy login sealed to a probe's key, as the server lists it (never the secret). */
export interface ProbeCredential {
  id: number;
  name: string;
  username: string;
  auth: 'password' | 'key';
  hosts: string[];
  key_fingerprint: string;
  /** Sealed to a key the probe no longer has. */
  stale: boolean;
  created_at: string | null;
  created_by: string | null;
  last_used_at: string | null;
}

/** The "Add a login" form; the secrets live here only until the login is sealed. */
export interface LoginForm {
  name: string;
  username: string;
  auth: 'password' | 'key';
  password: string;
  privateKey: string;
  passphrase: string;
  hosts: string;
}

const NAME = /^[A-Za-z0-9_.@-]{1,64}$/;

/** "10.0.0.0/24, a.lab  a.lab" → ["10.0.0.0/24", "a.lab"]. */
export function parseHosts(text: string): string[] {
  return [...new Set(text.split(/[\s,]+/).filter(Boolean))];
}

/** What is still missing, in words, or null when the login can be saved. */
export function loginFormProblem(f: LoginForm, localNames: string[], sealedNames: string[]): string | null {
  const name = f.name.trim();
  if (!name) return 'Give the login a name.';
  if (!NAME.test(name)) return 'The name may use letters, digits and _ . @ - (up to 64).';
  if (localNames.includes(name) || sealedNames.includes(name)) return 'A login with this name already exists.';
  if (!f.username.trim()) return 'Enter the username.';
  const hasPassword = f.password !== '';
  const hasKey = f.privateKey.trim() !== '';
  if (f.auth === 'password') {
    if (!hasPassword) return 'Enter the password.';
    if (hasKey) return 'Use only one of password and private key.';
  } else {
    if (!hasKey) return 'Paste or pick the private key.';
    if (hasPassword) return 'Use only one of password and private key.';
  }
  if (parseHosts(f.hosts).length === 0) return 'Enter at least one host the login may be used on.';
  return null;
}
