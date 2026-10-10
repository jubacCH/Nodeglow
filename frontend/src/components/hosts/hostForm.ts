/** Form state and validation for adding and editing a host (pure, tested). */

export const CHECK_TYPES: { value: string; label: string }[] = [
  { value: 'icmp', label: 'Ping (ICMP)' },
  { value: 'http', label: 'HTTP' },
  { value: 'https', label: 'HTTPS' },
  { value: 'tcp', label: 'TCP port' },
  { value: 'dns', label: 'DNS' },
];

export interface HostFormValues {
  name: string;
  hostname: string;
  check_type: string;
  port: string;
  latency_threshold_ms: string;
  enabled: boolean;
  /** '' = the core checks the host. */
  probe_id: string;
}

export const EMPTY_HOST_FORM: HostFormValues = {
  name: '',
  hostname: '',
  check_type: 'icmp',
  port: '',
  latency_threshold_ms: '',
  enabled: true,
  probe_id: '',
};

export type HostFormErrors = Partial<Record<keyof HostFormValues, string>>;

const HOSTNAME = /^[A-Za-z0-9._:\-[\]%]+$/;

function intInRange(raw: string, min: number, max: number): boolean {
  if (!/^\d+$/.test(raw.trim())) return false;
  const n = Number(raw);
  return n >= min && n <= max;
}

/**
 * Client-side checks; the backend validates again (SSRF rules, probes).
 * `mode` decides which fields exist: add has check type and port, edit has
 * threshold, enabled and probe.
 */
export function validateHostForm(v: HostFormValues, mode: 'add' | 'edit'): HostFormErrors {
  const e: HostFormErrors = {};
  const name = v.name.trim();
  const host = v.hostname.trim();
  if (!name) e.name = 'Enter a name.';
  else if (name.length > 120) e.name = 'Use at most 120 characters.';
  if (!host) e.hostname = 'Enter a hostname or IP address.';
  else if (host.length > 253) e.hostname = 'Use at most 253 characters.';
  else if (/^[a-z]+:\/\//i.test(host)) e.hostname = 'Enter the host only, without http:// (set the path in the HTTP options).';
  else if (!HOSTNAME.test(host)) e.hostname = 'Only letters, digits, dots, hyphens and colons; no spaces or slashes.';
  if (mode === 'add') {
    if (v.port.trim() && !intInRange(v.port, 1, 65535)) e.port = 'Port must be 1–65535.';
    if (v.check_type === 'tcp' && !v.port.trim()) e.port = 'A TCP check needs a port.';
  }
  if (v.latency_threshold_ms.trim() && !intInRange(v.latency_threshold_ms, 1, 60000)) {
    e.latency_threshold_ms = 'Threshold must be 1–60000 ms.';
  }
  return e;
}

export function hasErrors(e: HostFormErrors): boolean {
  return Object.values(e).some(Boolean);
}
