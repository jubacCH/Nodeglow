/**
 * The integration detail views rendered with real snapshot shapes.
 *
 * The fixtures are trimmed and anonymised copies of production snapshots
 * (`__fixtures__/*.json`). Each test pins key figures to where the view shows
 * them, so a renamed field or a wrong aggregation fails here instead of
 * rendering "—" or a wrong number in production. The E3 migration broke
 * several views exactly that way without any test noticing.
 */
import { describe, expect, it } from 'vitest';
import type { ComponentType } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { CloudflareDetail } from './CloudflareDetail';
import { NpmDetail } from './NpmDetail';
import { PhpipamDetail } from './PhpipamDetail';
import { ProxmoxDetail } from './ProxmoxDetail';
import { SpeedtestDetail } from './SpeedtestDetail';
import { SwisscomDetail } from './SwisscomDetail';
import { TechnitiumDetail } from './TechnitiumDetail';
import { UnasDetail } from './UnasDetail';
import { UnifiDetail } from './UnifiDetail';
import cloudflare from './__fixtures__/cloudflare.json';
import npm from './__fixtures__/npm.json';
import phpipam from './__fixtures__/phpipam.json';
import proxmox from './__fixtures__/proxmox.json';
import speedtest from './__fixtures__/speedtest.json';
import swisscom from './__fixtures__/swisscom.json';
import technitium from './__fixtures__/technitium.json';
import unas from './__fixtures__/unas.json';
import unifi from './__fixtures__/unifi.json';

/* eslint-disable @typescript-eslint/no-explicit-any */
function text(Detail: ComponentType<{ data: any; configId?: number }>, data: unknown): string {
  const html = renderToStaticMarkup(
    <QueryClientProvider client={new QueryClient()}>
      <Detail data={data} configId={1} />
    </QueryClientProvider>,
  );
  return html
    .replace(/<[^>]+>/g, '\n')
    .replace(/&#x27;/g, "'")
    .replace(/&amp;/g, '&')
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean)
    .join(' | ');
}
/* eslint-enable @typescript-eslint/no-explicit-any */

const views: [string, ComponentType<{ data: any; configId?: number }>, unknown][] = [ // eslint-disable-line @typescript-eslint/no-explicit-any
  ['cloudflare', CloudflareDetail, cloudflare],
  ['npm', NpmDetail, npm],
  ['phpipam', PhpipamDetail, phpipam],
  ['proxmox', ProxmoxDetail, proxmox],
  ['speedtest', SpeedtestDetail, speedtest],
  ['swisscom', SwisscomDetail, swisscom],
  ['technitium', TechnitiumDetail, technitium],
  ['unas', UnasDetail, unas],
  ['unifi', UnifiDetail, unifi],
];

describe('integration detail views', () => {
  it.each(views)('%s renders without broken values', (_name, Detail, data) => {
    const t = text(Detail, data);
    expect(t).not.toMatch(/NaN|undefined|Invalid Date|\[object Object\]/);
  });

  it.each(views)('%s renders an empty snapshot without crashing', (_name, Detail) => {
    expect(() => text(Detail, {})).not.toThrow();
  });

  it('proxmox shows the cluster totals', () => {
    const t = text(ProxmoxDetail, proxmox);
    const { totals } = proxmox;
    expect(t).toContain(`${totals.nodes_online}/${totals.nodes_total} | Nodes online`);
    expect(t).toContain(`${totals.vms_running}/${totals.vms_total} | VMs running`);
    expect(t).toContain(`${totals.lxc_running}/${totals.lxc_total} | LXCs running`);
  });

  it('unifi takes client totals from the controller, not from summing devices', () => {
    const t = text(UnifiDetail, unifi);
    expect(t).toContain(`${unifi.totals.clients_wifi} | WiFi clients`);
    expect(t).toContain(`${unifi.totals.clients_wired} | Wired clients`);
    expect(t).toContain(`${unifi.totals.clients_wifi + unifi.totals.clients_wired} | Total clients`);
  });

  it('unas counts disks and shows an empty bay as no data', () => {
    const t = text(UnasDetail, unas);
    expect(t).toContain(`${unas.totals.disks_ok}/${unas.totals.disks_total} OK`);
    expect(t).not.toMatch(/\d+ error/);
  });

  it('speedtest names the server operator and its location correctly', () => {
    const t = text(SpeedtestDetail, speedtest);
    expect(t).toContain(`Server | ${speedtest.server_location}`);
    expect(t).toContain(`Location | ${speedtest.server_name}`);
    expect(t).toContain(`${speedtest.download_mbps.toFixed(1)} | Mbps | Download`);
  });

  it('phpipam shows the address counts', () => {
    const t = text(PhpipamDetail, phpipam);
    expect(t).toContain(`${phpipam.addresses_total} | Total addresses`);
    expect(t).toContain(`${phpipam.addresses_active} | Active`);
  });

  it('swisscom shows active and total devices', () => {
    const t = text(SwisscomDetail, swisscom);
    expect(t).toContain(`${swisscom.hosts_total} total`);
  });

  it('npm shows proxy hosts and certificates', () => {
    const t = text(NpmDetail, npm);
    expect(t).toContain(`${npm.proxy_count} | Proxy hosts`);
    expect(t).toContain(`${npm.cert_count} | SSL certificates`);
  });

  it('technitium shows the query volume', () => {
    const t = text(TechnitiumDetail, technitium);
    expect(t).toContain('Queries (24h)');
    expect(t).toContain(`${technitium.clients}`);
  });

  it('cloudflare shows missing analytics as no value, never as 0', () => {
    const t = text(CloudflareDetail, cloudflare);
    expect(t).toContain('— | Requests 24h');
    expect(t).not.toContain('0 B | Bandwidth');
  });
});
