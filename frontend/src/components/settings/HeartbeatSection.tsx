'use client';

import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/Button';
import { Field, Input, Select } from '@/components/ui/Field';
import { apiErrorMessage, get, post } from '@/lib/api';
import { Notice, SettingsSection } from './formKit';

export interface HeartbeatState {
  mode: 'unset' | 'off' | 'set';
  source: 'setting' | 'environment' | 'none';
  last_ok_seconds_ago: number | null;
  failures_in_a_row: number;
  last_error: string | null;
}

type Choice = 'url' | 'off';

/** One line on how the monitor fares, and the tone to show it in. */
export function heartbeatSummary(s: HeartbeatState): { tone: 'info' | 'warning' | 'ok' | 'down'; text: string } {
  if (s.mode === 'unset') {
    return { tone: 'warning', text: 'No external monitor is set up. An outage of Nodeglow itself is reported by nobody.' };
  }
  if (s.mode === 'off') {
    return { tone: 'info', text: 'Running without an external monitor. An outage of Nodeglow itself is reported by nobody.' };
  }
  if (s.failures_in_a_row >= 5) {
    return { tone: 'down', text: `The last ${s.failures_in_a_row} requests failed: ${s.last_error ?? 'no error text'}.` };
  }
  if (s.last_ok_seconds_ago === null) {
    return { tone: 'info', text: 'A monitor URL is saved. No request has been answered yet; the next one goes out within a minute.' };
  }
  return { tone: 'ok', text: `The monitor answered the last request ${s.last_ok_seconds_ago} s ago.` };
}

/**
 * Who is told when Nodeglow itself stops: a dead-man service outside,
 * requested once a minute. Saved on its own, not with the form around it.
 */
export function HeartbeatSection() {
  const qc = useQueryClient();
  const query = useQuery<HeartbeatState>({
    queryKey: ['settings-heartbeat'],
    queryFn: () => get<HeartbeatState>('/settings/heartbeat'),
    refetchInterval: 60_000,
  });
  const state = query.data;

  const [choice, setChoice] = useState<Choice>('url');
  const [url, setUrl] = useState('');
  const [busy, setBusy] = useState<'save' | 'test' | null>(null);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    if (state) setChoice(state.mode === 'off' ? 'off' : 'url');
  }, [state?.mode]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!state) return null;
  const summary = heartbeatSummary(state);
  const saved = state.mode === 'set';

  async function save() {
    setBusy('save');
    setResult(null);
    try {
      const next = await post<HeartbeatState>('/settings/heartbeat/save', choice === 'off' ? { mode: 'off' } : { mode: 'url', url });
      qc.setQueryData(['settings-heartbeat'], next);
      setUrl('');
      setResult({ ok: true, text: 'Saved.' });
    } catch (e) {
      setResult({ ok: false, text: apiErrorMessage(e, 'Could not save') });
    } finally {
      setBusy(null);
    }
  }

  async function test() {
    setBusy('test');
    setResult(null);
    try {
      const res = await post<{ ok: boolean; error: string | null }>('/settings/heartbeat/test', {});
      setResult(res.ok ? { ok: true, text: 'The monitor answered.' } : { ok: false, text: `The request failed: ${res.error ?? 'no error text'}.` });
      qc.invalidateQueries({ queryKey: ['settings-heartbeat'] });
    } catch (e) {
      setResult({ ok: false, text: apiErrorMessage(e, 'The request failed') });
    } finally {
      setBusy(null);
    }
  }

  return (
    <SettingsSection
      id="heartbeat"
      title="External monitor"
      description="Nodeglow reports its own problems through itself. When it stops altogether, somebody outside has to notice: a dead-man service (healthchecks.io, an Uptime Kuma push monitor, another Nodeglow) that is requested once a minute and alerts you when the requests stop."
    >
      <div className="grid max-w-xl gap-4">
        <Notice tone={summary.tone}>{summary.text}</Notice>
        <Field label="When Nodeglow stops">
          <Select value={choice} onChange={(e) => setChoice(e.target.value as Choice)}>
            <option value="url">An external monitor reports it</option>
            <option value="off">Nobody is told (run without a monitor)</option>
          </Select>
        </Field>
        {choice === 'url' && (
          <Field
            label="Check URL of the monitor"
            hint={saved
              ? `A URL is saved${state.source === 'environment' ? ' in the environment (NODEGLOW_HEARTBEAT_URL)' : ''}. It is not shown again; enter a new one to replace it.`
              : 'The URL the monitor gives you for this check. Give the check a grace time of about five minutes: an update restarts the backend.'}
          >
            <Input
              inputMode="url"
              autoComplete="off"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder={saved ? 'Saved' : 'https://hc-ping.com/your-uuid'}
            />
          </Field>
        )}
        {result && <Notice tone={result.ok ? 'ok' : 'down'}>{result.text}</Notice>}
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            loading={busy === 'save'}
            disabled={busy !== null || (choice === 'url' && !url.trim()) || (choice === 'off' && state.mode === 'off')}
            onClick={save}
          >
            Save
          </Button>
          <Button variant="secondary" size="sm" loading={busy === 'test'} disabled={busy !== null || !saved} onClick={test}>
            Send a test request
          </Button>
        </div>
      </div>
    </SettingsSection>
  );
}
