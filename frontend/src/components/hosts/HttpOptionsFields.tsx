'use client';

import { Checkbox, Field, Input, Select } from '@/components/ui/Field';
import type { HttpOptionsForm } from '@/lib/httpOptions';

/** Inputs for the per-host HTTP(S) check options. Controlled; no state of its own. */
export function HttpOptionsFields({ value, onChange }: {
  value: HttpOptionsForm;
  onChange: (next: HttpOptionsForm) => void;
}) {
  const set = <K extends keyof HttpOptionsForm>(key: K, v: HttpOptionsForm[K]) =>
    onChange({ ...value, [key]: v });
  const noBody = value.method === 'HEAD';

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <Field label="Method">
        <Select value={value.method} onChange={(e) => set('method', e.target.value === 'HEAD' ? 'HEAD' : 'GET')}>
          <option value="GET">GET</option>
          <option value="HEAD">HEAD</option>
        </Select>
      </Field>
      <Field label="Path or URL">
        <Input type="text" placeholder="/health" value={value.url} onChange={(e) => set('url', e.target.value)} />
      </Field>
      <Field label="Expected status" hint="Default: any status below 500">
        <Input
          type="text"
          placeholder="200-299,301"
          value={value.expected_status}
          onChange={(e) => set('expected_status', e.target.value)}
        />
      </Field>
      <Field label="Timeout (s)">
        <Input type="number" inputMode="numeric" min={1} max={60} value={value.timeout} onChange={(e) => set('timeout', e.target.value)} />
      </Field>
      <Field label="Body must contain" hint={noBody ? 'Needs GET (HEAD has no body)' : undefined}>
        <Input type="text" placeholder="OK" value={value.keyword} disabled={noBody} onChange={(e) => set('keyword', e.target.value)} />
      </Field>
      <Field label="Body must not contain" hint={noBody ? 'Needs GET (HEAD has no body)' : undefined}>
        <Input
          type="text"
          placeholder="error"
          value={value.keyword_absent}
          disabled={noBody}
          onChange={(e) => set('keyword_absent', e.target.value)}
        />
      </Field>
      <Checkbox
        label="Follow redirects (max 5)"
        checked={value.follow_redirects}
        onChange={(e) => set('follow_redirects', e.target.checked)}
      />
      <Checkbox
        label="Verify TLS certificate"
        checked={value.verify_tls}
        onChange={(e) => set('verify_tls', e.target.checked)}
      />
      <p className="text-meta text-fg-3 sm:col-span-2">
        Keywords are case-insensitive and searched in the first 256 kB of the response.
        Redirects to loopback, link-local or metadata addresses are never followed.
      </p>
    </div>
  );
}
