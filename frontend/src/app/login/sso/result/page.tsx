'use client';

import { useEffect, useState } from 'react';
import { Lockup } from '@/components/ui/NodeglowMark';
import { Notice } from '@/components/settings/formKit';
import { ApiError, get } from '@/lib/api';
import { callbackErrorText, verdict, type TestOutcome, type TestResultResponse } from '@/lib/sso';

/**
 * Where the identity provider's callback ends: the result of a test sign-in.
 *
 * Under /login on purpose. The browser arrives here through a redirect chain
 * that started on the provider's site, so the SameSite=Strict session cookie
 * is not sent with this page load; the fetch below, made from this page, is
 * same-site and carries it. Only the admin who started the test sees its
 * result.
 */
type View =
  | { kind: 'loading' }
  | { kind: 'error'; text: string }
  | { kind: 'result'; data: TestResultResponse };

export default function SsoResultPage() {
  const [view, setView] = useState<View>({ kind: 'loading' });

  useEffect(() => {
    document.title = 'Test sign-in | Nodeglow';
    const params = new URLSearchParams(window.location.search);
    const flow = params.get('flow');
    if (!flow || !/^\d+$/.test(flow)) {
      setView({ kind: 'error', text: callbackErrorText(params.get('error')) });
      return;
    }
    get<TestResultResponse>(`/api/identity-providers/tests/${flow}`)
      .then((data) => setView({ kind: 'result', data }))
      .catch((e) => {
        const status = e instanceof ApiError ? e.status : 0;
        setView({
          kind: 'error',
          text: status === 401
            ? 'Sign in to Nodeglow with the account that started the test, then open the result again from the settings.'
            : 'This result is not available: it belongs to another account, or it is older than an hour.',
        });
      });
  }, []);

  return (
    <main className="flex min-h-screen items-start justify-center bg-bg px-4 py-12">
      <div className="w-full max-w-2xl space-y-5">
        <div className="flex flex-col items-center gap-2 text-center">
          <Lockup size={32} />
          <h1 className="text-body font-medium text-fg">Test sign-in</h1>
        </div>
        <div className="space-y-4 rounded-ng-lg border border-border bg-surface p-6">
          {view.kind === 'loading' && <p className="text-ui text-fg-2">Loading the result…</p>}
          {view.kind === 'error' && <Notice tone="down">{view.text}</Notice>}
          {view.kind === 'result' && <Result data={view.data} />}
          <a href="/settings?tab=auth" className="inline-block text-ui text-accent hover:text-accent-hover">
            Back to the authentication settings
          </a>
        </div>
      </div>
    </main>
  );
}

function Result({ data }: { data: TestResultResponse }) {
  if (!data.done || !data.result) {
    return <Notice tone="info">The provider has not sent the browser back yet.</Notice>;
  }
  const outcome = data.result;
  const v = verdict(outcome);
  return (
    <div className="space-y-4">
      <p className="text-ui text-fg-2">Provider: <span className="font-medium text-fg">{data.provider.name}</span></p>
      <Notice tone={v.tone}>{v.text}</Notice>
      {!outcome.ok && outcome.provider_error && (
        <p className="text-meta text-fg-2">
          The provider said: <code className="font-mono">{outcome.provider_error}</code>
          {outcome.provider_error_description && <> — {outcome.provider_error_description}</>}
        </p>
      )}
      {outcome.claims && <Claims outcome={outcome} />}
      {outcome.preview && (
        <section aria-label="What the current settings would decide">
          <h2 className="mb-1 text-ui font-medium text-fg">With the current settings</h2>
          <ul className="list-disc space-y-0.5 pl-5 text-ui text-fg-2">
            <li>{outcome.preview.would_sign_in ? 'This person would get in' : 'This person would not get in'}
              {outcome.preview.would_sign_in && outcome.preview.role && <> as <strong>{outcome.preview.role}</strong>
                {outcome.preview.role_from === 'rules' ? ' (from a group rule)' : ' (the role without a rule)'}</>}
              .</li>
            {outcome.preview.reasons.map((r) => <li key={r}>{r}</li>)}
          </ul>
          <p className="mt-2 text-meta text-fg-3">A test creates no account and no session.</p>
        </section>
      )}
      {outcome.notes && outcome.notes.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-5 text-meta text-fg-2">
          {outcome.notes.map((n) => <li key={n}>{n}</li>)}
        </ul>
      )}
    </div>
  );
}

function Claims({ outcome }: { outcome: TestOutcome }) {
  const c = outcome.claims!;
  const verified = c.email_verified === true ? 'verified' : c.email_verified === false ? 'not verified' : 'not stated';
  const rows: [string, string][] = [
    ['Subject (sub)', c.sub ?? '—'],
    ['Mail address', c.email ? `${c.email} (${verified})` : '—'],
    ['Mail domain', outcome.preview?.mail_domain ?? '—'],
    ['Name', c.name ?? '—'],
    ['Username at the provider', c.preferred_username ?? '—'],
    [`Groups (claim "${c.groups_claim}")`, c.groups_present ? (c.groups.join(', ') || 'empty') : 'not sent'],
  ];
  return (
    <section aria-label="What the provider sent">
      <h2 className="mb-1 text-ui font-medium text-fg">What the provider sent</h2>
      <dl className="grid grid-cols-[minmax(0,12rem)_1fr] gap-x-4 gap-y-1 text-ui">
        {rows.map(([k, val]) => (
          <div key={k} className="contents">
            <dt className="text-fg-3">{k}</dt>
            <dd className="break-words font-mono text-meta text-fg">{val}</dd>
          </div>
        ))}
      </dl>
      {outcome.userinfo_used && <p className="mt-1 text-meta text-fg-3">Some claims came from the provider’s user info.</p>}
    </section>
  );
}
