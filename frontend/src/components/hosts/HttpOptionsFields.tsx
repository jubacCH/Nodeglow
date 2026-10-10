'use client';

import { useId } from 'react';
import type { HttpOptionsForm } from '@/lib/httpOptions';

const inputClass = 'ng-input';
const selectClass = 'ng-input [&>option]:text-[var(--ng-text-primary)]';

/** Inputs for the per-host HTTP(S) check options. Controlled; no state of its own. */
export function HttpOptionsFields({ value, onChange }: {
  value: HttpOptionsForm;
  onChange: (next: HttpOptionsForm) => void;
}) {
  const id = useId();
  const set = <K extends keyof HttpOptionsForm>(key: K, v: HttpOptionsForm[K]) =>
    onChange({ ...value, [key]: v });

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
      <div>
        <label htmlFor={`${id}-method`} className="ng-label">Method</label>
        <select
          id={`${id}-method`}
          value={value.method}
          onChange={(e) => set('method', e.target.value === 'HEAD' ? 'HEAD' : 'GET')}
          className={selectClass}
        >
          <option value="GET">GET</option>
          <option value="HEAD">HEAD</option>
        </select>
      </div>
      <div>
        <label htmlFor={`${id}-url`} className="ng-label">Path or URL</label>
        <input
          id={`${id}-url`}
          type="text"
          placeholder="/health"
          value={value.url}
          onChange={(e) => set('url', e.target.value)}
          className={inputClass}
        />
      </div>
      <div>
        <label htmlFor={`${id}-status`} className="ng-label">Expected status</label>
        <input
          id={`${id}-status`}
          type="text"
          placeholder="any below 500, e.g. 200-299,301"
          value={value.expected_status}
          onChange={(e) => set('expected_status', e.target.value)}
          className={inputClass}
        />
      </div>
      <div>
        <label htmlFor={`${id}-timeout`} className="ng-label">Timeout (s)</label>
        <input
          id={`${id}-timeout`}
          type="number"
          min={1}
          max={60}
          value={value.timeout}
          onChange={(e) => set('timeout', e.target.value)}
          className={inputClass}
        />
      </div>
      <div>
        <label htmlFor={`${id}-kw`} className="ng-label">Body must contain</label>
        <input
          id={`${id}-kw`}
          type="text"
          placeholder="e.g. OK"
          value={value.keyword}
          disabled={value.method === 'HEAD'}
          onChange={(e) => set('keyword', e.target.value)}
          className={inputClass}
        />
      </div>
      <div>
        <label htmlFor={`${id}-kwn`} className="ng-label">Body must not contain</label>
        <input
          id={`${id}-kwn`}
          type="text"
          placeholder="e.g. error"
          value={value.keyword_absent}
          disabled={value.method === 'HEAD'}
          onChange={(e) => set('keyword_absent', e.target.value)}
          className={inputClass}
        />
      </div>
      <label className="flex items-center gap-2 text-sm text-slate-300">
        <input
          type="checkbox"
          checked={value.follow_redirects}
          onChange={(e) => set('follow_redirects', e.target.checked)}
          className="rounded border-white/20 bg-white/[0.06]"
        />
        Follow redirects (max 5)
      </label>
      <label className="flex items-center gap-2 text-sm text-slate-300">
        <input
          type="checkbox"
          checked={value.verify_tls}
          onChange={(e) => set('verify_tls', e.target.checked)}
          className="rounded border-white/20 bg-white/[0.06]"
        />
        Verify TLS certificate
      </label>
      <p className="sm:col-span-2 text-xs text-slate-500">
        Keywords are case-insensitive and searched in the first 256 kB of the response.
        Redirects to loopback, link-local or metadata addresses are never followed.
      </p>
    </div>
  );
}
