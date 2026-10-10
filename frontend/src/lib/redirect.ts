/**
 * Post-login redirect helpers. The original path travels as `?next=` on
 * /login and is honoured after a successful login — but only when it is a
 * same-origin relative path, so /login?next=https://evil.example cannot be
 * turned into an open redirect.
 */

const LOGIN_PATH = '/login';

/**
 * Return `raw` if it is a safe same-origin path (e.g. "/hosts/12?tab=x"),
 * otherwise null. Rejects absolute and protocol-relative URLs ("//x",
 * "/\x"), anything with a scheme or control characters, and /login itself.
 */
export function safeNextPath(raw: string | null | undefined): string | null {
  if (!raw || typeof raw !== 'string') return null;
  if (raw.length > 2048) return null;
  if (!raw.startsWith('/')) return null;
  // "//host" and "/\host" are treated as protocol-relative by browsers.
  if (raw.startsWith('//') || raw.startsWith('/\\')) return null;
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(raw)) return null;
  let parsed: URL;
  try {
    parsed = new URL(raw, 'http://nodeglow.invalid');
  } catch {
    return null;
  }
  if (parsed.origin !== 'http://nodeglow.invalid') return null;
  if (parsed.pathname === LOGIN_PATH || parsed.pathname.startsWith(`${LOGIN_PATH}/`)) return null;
  return parsed.pathname + parsed.search + parsed.hash;
}

/** "/login", or "/login?next=<path>" when `path` is worth returning to. */
export function loginHref(path: string | null | undefined): string {
  const next = safeNextPath(path);
  if (!next || next === '/') return LOGIN_PATH;
  return `${LOGIN_PATH}?next=${encodeURIComponent(next)}`;
}
