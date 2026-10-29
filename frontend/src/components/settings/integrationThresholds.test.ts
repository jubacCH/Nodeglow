import { describe, expect, it } from 'vitest';
import { groupThresholds, thresholdPayload, type ThresholdRow } from './IntegrationThresholds';

const row = (id: string, label: string, extra: Partial<ThresholdRow> = {}): ThresholdRow => ({
  id, integration: id.split('.')[0], integration_label: label, key: id.split('.')[1], label: id,
  unit: '%', default: 90, value: 90, min: 1, max: 100, zero_disables: false, ...extra,
});

describe('integration thresholds form', () => {
  const rows = [row('proxmox.node_cpu_pct', 'Proxmox VE'), row('npm.cert_warn_days', 'NPM'),
    row('proxmox.node_mem_pct', 'Proxmox VE'), row('speedtest.min_download_mbps', 'Speedtest', { min: 0, max: null })];

  it('groups by integration in server order', () => {
    expect(groupThresholds(rows).map(([label, g]) => [label, g.length])).toEqual([['Proxmox VE', 2], ['NPM', 1], ['Speedtest', 1]]);
  });

  it('sends numbers and refuses empty or out-of-range fields', () => {
    const ok = { 'proxmox.node_cpu_pct': '80', 'npm.cert_warn_days': '14', 'proxmox.node_mem_pct': '95', 'speedtest.min_download_mbps': '0' };
    expect(thresholdPayload(rows, ok)).toEqual({ values: { 'proxmox.node_cpu_pct': 80, 'npm.cert_warn_days': 14, 'proxmox.node_mem_pct': 95, 'speedtest.min_download_mbps': 0 } });
    expect(thresholdPayload(rows, { ...ok, 'proxmox.node_cpu_pct': '150', 'npm.cert_warn_days': '' }))
      .toEqual({ invalid: ['proxmox.node_cpu_pct', 'npm.cert_warn_days'] });
    expect(thresholdPayload(rows, { ...ok, 'speedtest.min_download_mbps': '5000' })).toHaveProperty('values');
  });
});
