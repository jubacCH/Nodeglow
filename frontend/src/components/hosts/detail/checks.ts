/** Parse "icmp,https,tcp:443" into the configured checks and TCP ports. */
export function parseCheckTypes(checkType: string | null | undefined, legacyPort: number | null) {
  const types = (checkType || 'icmp').split(',').map((t) => t.trim()).filter(Boolean);
  const ports = types
    .filter((t) => t === 'tcp' || t.startsWith('tcp:'))
    .map((t) => (t.includes(':') ? Number.parseInt(t.split(':')[1], 10) : legacyPort ?? 0))
    .filter((p) => Number.isInteger(p) && p > 0);
  return { types, ports: [...new Set(ports)] };
}
