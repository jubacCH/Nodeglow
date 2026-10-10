import type { HttpOptions } from '@/types';

/** Form state for the HTTP check options (strings, as inputs hold them). */
export interface HttpOptionsForm {
  method: 'GET' | 'HEAD';
  url: string;
  expected_status: string;
  keyword: string;
  keyword_absent: string;
  timeout: string;
  follow_redirects: boolean;
  verify_tls: boolean;
}

export const DEFAULT_HTTP_FORM: HttpOptionsForm = {
  method: 'GET',
  url: '',
  expected_status: '',
  keyword: '',
  keyword_absent: '',
  timeout: '5',
  follow_redirects: true,
  verify_tls: false,
};

export function httpFormFromOptions(opts: Partial<HttpOptions> | null | undefined): HttpOptionsForm {
  if (!opts) return { ...DEFAULT_HTTP_FORM };
  return {
    method: opts.method === 'HEAD' ? 'HEAD' : 'GET',
    url: opts.url ?? '',
    expected_status: opts.expected_status ?? '',
    keyword: opts.keyword ?? '',
    keyword_absent: opts.keyword_absent ?? '',
    timeout: opts.timeout != null ? String(opts.timeout) : DEFAULT_HTTP_FORM.timeout,
    follow_redirects: opts.follow_redirects ?? true,
    verify_tls: opts.verify_tls ?? false,
  };
}

/** The request payload; the backend drops values equal to the defaults. */
export function httpOptionsPayload(form: HttpOptionsForm): Partial<HttpOptions> {
  const timeout = Number(form.timeout);
  return {
    method: form.method,
    url: form.url.trim() || null,
    expected_status: form.expected_status.trim() || null,
    keyword: form.keyword.trim() ? form.keyword : null,
    keyword_absent: form.keyword_absent.trim() ? form.keyword_absent : null,
    timeout: Number.isFinite(timeout) && form.timeout.trim() ? timeout : 5,
    follow_redirects: form.follow_redirects,
    verify_tls: form.verify_tls,
  };
}

/** True if the check type list contains an http or https check. */
export function hasHttpCheck(checkType: string | null | undefined): boolean {
  return (checkType ?? '').split(',').some((t) => ['http', 'https'].includes(t.trim().toLowerCase()));
}

const STATUS_PART = /^(\d{3})(?:\s*-\s*(\d{3}))?$/;

/**
 * Client-side check mirroring the backend's rules, so obvious mistakes show
 * up before saving. Returns an error message, or null when the form is fine.
 */
export function validateHttpForm(form: HttpOptionsForm): string | null {
  const status = form.expected_status.trim();
  if (status) {
    const parts = status.split(',').map((p) => p.trim()).filter(Boolean);
    if (parts.length === 0 || parts.length > 20) return 'Expected status: list 1–20 codes or ranges';
    for (const part of parts) {
      const m = STATUS_PART.exec(part);
      if (!m) return `Expected status: "${part}" is not a code or range`;
      const lo = Number(m[1]);
      const hi = Number(m[2] ?? m[1]);
      if (lo < 100 || hi > 599 || lo > hi) return `Expected status: "${part}" is outside 100–599`;
    }
  }
  const timeout = Number(form.timeout);
  if (form.timeout.trim() && (!Number.isFinite(timeout) || timeout < 1 || timeout > 60)) {
    return 'Timeout must be between 1 and 60 seconds';
  }
  if (form.keyword.length > 200 || form.keyword_absent.length > 200) {
    return 'Keywords are limited to 200 characters';
  }
  if (form.method === 'HEAD' && (form.keyword.trim() || form.keyword_absent.trim())) {
    return 'Keyword checks need GET — a HEAD response has no body';
  }
  const url = form.url.trim();
  if (url && !url.startsWith('/') && !/^https?:\/\/[^/\s]+/i.test(url)) {
    return 'Path must start with "/" or be a full http(s):// URL';
  }
  if (url.startsWith('//') || /\s/.test(url)) return 'Path must start with a single "/" and contain no spaces';
  return null;
}
