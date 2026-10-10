import { readdirSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { describe, expect, it } from 'vitest';
import { NAV_SECTIONS, findActive, shortcutRoutes, visibleSections } from './navigation';

const APP_DIR = join(__dirname, '..', 'app', '(app)');

/** Every page route under app/(app), with dynamic segments filled in. */
function appRoutes(dir = APP_DIR): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...appRoutes(full));
    else if (name === 'page.tsx') {
      const rel = relative(APP_DIR, dir).split(sep).filter(Boolean);
      out.push('/' + rel.map((seg) => (seg === '[type]' ? 'proxmox' : seg.startsWith('[') ? '1' : seg)).join('/'));
    }
  }
  return out;
}

describe('navigation registry', () => {
  it('makes every existing route reachable', () => {
    const routes = appRoutes();
    expect(routes.length).toBeGreaterThan(20);
    const unmapped = routes.filter((r) => !findActive(r));
    expect(unmapped).toEqual([]);
  });

  it('marks detail pages under their list item', () => {
    expect(findActive('/incidents/42')?.item.id).toBe('incidents');
    expect(findActive('/hosts/7')?.item.id).toBe('hosts');
    expect(findActive('/integration/unifi/3')?.item.id).toBe('integrations');
    expect(findActive('/syslog/templates')?.item.id).toBe('log-patterns');
    expect(findActive('/')?.section.id).toBe('overview');
    expect(findActive('/changes')?.item.id).toBe('changes');
    expect(findActive('/')?.item.id).toBe('dashboard');
  });

  it('prefers the item whose query matches', () => {
    expect(findActive('/alerts', new URLSearchParams('tab=maintenance'))?.item.id).toBe('maintenance');
    expect(findActive('/alerts', new URLSearchParams('tab=incidents'))?.item.id).toBe('incidents');
  });

  it('hides admin-only items for other roles', () => {
    const admin = visibleSections(false).find((s) => s.id === 'admin');
    expect(admin?.items.map((i) => i.id)).toEqual(['credentials', 'system']);
  });

  it('puts five sections in the mobile tab bar', () => {
    expect(NAV_SECTIONS.filter((s) => s.mobile)).toHaveLength(5);
  });

  it('keeps the old g-shortcuts working', () => {
    const r = shortcutRoutes();
    expect(r.d.href).toBe('/');
    expect(r.h.href).toBe('/hosts');
    expect(r.a.href).toBe('/alerts');
    expect(r.s.href).toBe('/syslog');
    expect(r.t.href).toBe('/system/status');
  });
});
