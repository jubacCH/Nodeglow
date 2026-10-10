'use client';

import { useState, useEffect, type FormEvent } from 'react';
import { useRouter } from 'next/navigation';
import { Button } from '@/components/ui/Button';
import { Field, Input } from '@/components/ui/Field';
import { Lockup } from '@/components/ui/NodeglowMark';
import { useAuthStore } from '@/stores/auth';
import { safeNextPath } from '@/lib/redirect';

export default function LoginPage() {
  const router = useRouter();
  const fetchUser = useAuthStore((s) => s.fetchUser);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetch('/setup/status')
      .then((r) => r.json())
      .then((d) => {
        if (!d.setup_complete) window.location.href = '/setup';
      })
      .catch(() => {});
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
        credentials: 'include',
      });

      if (res.ok) {
        await fetchUser();
        // Return to the page that sent us here (?next=), same-origin only.
        const next = safeNextPath(new URLSearchParams(window.location.search).get('next'));
        router.push(next ?? '/');
        router.refresh();
      } else {
        const data = await res.json().catch(() => ({}));
        setError(data.error || 'Invalid username or password');
      }
    } catch {
      setError('Connection failed');
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-bg px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center gap-2 text-center">
          <Lockup size={40} wordmarkClassName="text-[26px]" />
          <h1 className="sr-only">Sign in to Nodeglow</h1>
          <p className="text-ui text-fg-2">Infrastructure monitoring</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4 rounded-ng-lg border border-border bg-surface p-6" aria-label="Sign in">
          {error && (
            <div role="alert" className="rounded-ctl border border-down/30 bg-down-soft px-3 py-2 text-ui text-down">
              {error}
            </div>
          )}

          <Field label="Username">
            <Input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              // eslint-disable-next-line jsx-a11y/no-autofocus
              autoFocus
              required
            />
          </Field>

          <Field label="Password">
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </Field>

          <Button type="submit" className="w-full" loading={loading}>
            {loading ? 'Signing in…' : 'Sign in'}
          </Button>
        </form>
      </div>
    </main>
  );
}
