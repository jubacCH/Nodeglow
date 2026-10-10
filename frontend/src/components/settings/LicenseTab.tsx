'use client';

import { useState, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/Button';
import { CopyButton } from '@/components/ui/CopyButton';
import { Field, Textarea } from '@/components/ui/Field';
import { QueryState } from '@/components/ui/QueryState';
import { StatusPill } from '@/components/ui/StatusPill';
import { Tag } from '@/components/ui/Tag';
import { FEATURES_KEY, useFeatures, type LicenseState } from '@/hooks/queries/useFeatures';
import { useConfirm } from '@/hooks/useConfirm';
import { apiErrorMessage, del, get, post } from '@/lib/api';
import type { HealthState } from '@/lib/status';
import { useToastStore } from '@/stores/toast';
import { Code, Notice, SettingsSection } from './formKit';

/** GET /settings/license (enterprise backend, admins only). */
export interface LicenseDetail {
  status: LicenseState;
  message: string;
  edition: string;
  source: 'environment' | 'settings' | null;
  error: string | null;
  license_id: string | null;
  customer: string | null;
  features: string[];
  all_features: boolean;
  max_tenants: number | null;
  issued_at: string | null;
  expires_at: string | null;
  grace_until: string | null;
  days_left: number | null;
  grace_days: number;
  install_id: string | null;
  install_id_bound: boolean;
  managed_by_env: boolean;
  env_var: string;
}

export const LICENSE_KEY = ['license'] as const;

const STATUS_PILL: Record<LicenseState, { state: HealthState; label: string }> = {
  valid: { state: 'ok', label: 'Valid' },
  grace: { state: 'warning', label: 'Grace period' },
  expired: { state: 'down', label: 'Expired' },
  invalid: { state: 'down', label: 'Invalid' },
  missing: { state: 'unknown', label: 'No license' },
  error: { state: 'unknown', label: 'Unknown' },
};

const FEATURE_LABELS: Record<string, string> = {
  ha_scheduler: 'HA scheduler',
  ai_assistant: 'Glow',
  ai_postmortem: 'AI postmortems',
  ai_daily_summary: 'AI daily summary',
};

function day(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' });
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[140px_1fr] gap-3 py-1.5 max-[559px]:grid-cols-1 max-[559px]:gap-0.5">
      <dt className="text-meta text-fg-3">{label}</dt>
      <dd className="min-w-0 text-ui text-fg">{children}</dd>
    </div>
  );
}

export function LicenseTab() {
  const { data: features } = useFeatures();
  const enterprise = features?.edition === 'enterprise';
  const query = useQuery<LicenseDetail>({
    queryKey: LICENSE_KEY,
    queryFn: () => get('/settings/license'),
    enabled: enterprise,
  });

  if (features && !enterprise) {
    return (
      <SettingsSection id="license" title="License">
        <Notice tone="info">
          <span data-testid="license-community">
            This is the community edition. Enterprise features and license keys are not part of this build.
          </span>
        </Notice>
      </SettingsSection>
    );
  }

  return (
    <QueryState query={query} errorTitle="Could not load the license">
      {(lic) => <LicenseDetails lic={lic} />}
    </QueryState>
  );
}

function LicenseDetails({ lic }: { lic: LicenseDetail }) {
  const toast = useToastStore();
  const qc = useQueryClient();
  const { confirm, ConfirmDialogElement } = useConfirm();
  const [text, setText] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const pill = STATUS_PILL[lic.status] ?? STATUS_PILL.error;
  const hasLicense = !!lic.license_id;

  function refresh(next: LicenseDetail) {
    qc.setQueryData(LICENSE_KEY, next);
    qc.invalidateQueries({ queryKey: FEATURES_KEY });
  }

  async function install() {
    setBusy(true);
    setError(null);
    try {
      refresh(await post<LicenseDetail>('/settings/license', { license: text.trim() }));
      setText('');
      toast.show('License installed', 'success');
    } catch (e) {
      setError(apiErrorMessage(e, 'The license could not be installed'));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    const ok = await confirm({
      title: 'Remove the license?',
      description: 'Enterprise features stop working right away. Monitoring and existing data are not affected.',
      confirmLabel: 'Remove license',
      variant: 'danger',
    });
    if (!ok) return;
    try {
      refresh(await del<LicenseDetail>('/settings/license'));
      toast.show('License removed', 'success');
    } catch (e) {
      toast.show(apiErrorMessage(e, 'The license could not be removed'), 'error');
    }
  }

  return (
    <div className="space-y-5">
      <SettingsSection
        id="license"
        title="License"
        description="Nodeglow Enterprise features are enabled by a signed license key, verified offline on this server."
        actions={<StatusPill status={pill.state}>{pill.label}</StatusPill>}
      >
        <p className="mb-3 max-w-3xl text-ui text-fg-2" data-testid="license-message">{lic.message}</p>
        <dl className="max-w-3xl">
          <Row label="Edition">Enterprise</Row>
          {hasLicense && (
            <>
              <Row label="Customer">{lic.customer}</Row>
              <Row label="License ID"><Code>{lic.license_id}</Code></Row>
              <Row label="Expires">
                {day(lic.expires_at)}
                {lic.status === 'valid' && lic.days_left !== null && (
                  <span className="text-fg-3"> · {lic.days_left} days left</span>
                )}
                {(lic.status === 'grace' || lic.status === 'expired') && (
                  <span className="text-fg-3"> · grace period until {day(lic.grace_until)}</span>
                )}
              </Row>
              <Row label="Features">
                <span className="flex flex-wrap gap-1.5">
                  {lic.features.map((f) => <Tag key={f}>{FEATURE_LABELS[f] ?? f}</Tag>)}
                  {lic.all_features && <span className="text-meta text-fg-3">(all, including future features)</span>}
                </span>
              </Row>
              <Row label="Tenants">{lic.max_tenants ?? 'Unlimited'}</Row>
            </>
          )}
          {lic.install_id && (
            <Row label="Installation ID">
              <span className="inline-flex items-center gap-1.5">
                <Code>{lic.install_id}</Code>
                <CopyButton text={lic.install_id} />
              </span>
            </Row>
          )}
          <Row label="Source">
            {lic.source === 'environment' ? <>Environment variable <Code>{lic.env_var}</Code></>
              : lic.source === 'settings' ? 'Installed here' : '—'}
          </Row>
        </dl>
      </SettingsSection>

      {lic.managed_by_env ? (
        <Notice tone="info">
          This license is set by the <Code>{lic.env_var}</Code> environment variable. To replace it, change the
          variable and restart the backend.
        </Notice>
      ) : (
        <SettingsSection
          id="license-install"
          title={hasLicense ? 'Replace license' : 'Install license'}
          description="Paste the license key you received. It replaces the current one and is recorded in the audit log."
          actions={hasLicense ? (
            <Button size="sm" variant="ghost" onClick={remove}>Remove license</Button>
          ) : undefined}
        >
          <Field label="License key" error={error}>
            <Textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={4}
              spellCheck={false}
              className="font-mono text-meta"
              placeholder="eyJmb3JtYXQiOiJub2RlZ2xvdy1saWNlbnNlLzEi…"
            />
          </Field>
          <div className="mt-3 flex justify-end">
            <Button onClick={install} disabled={busy || !text.trim()}>
              {busy ? 'Installing…' : 'Install license'}
            </Button>
          </div>
        </SettingsSection>
      )}
      {ConfirmDialogElement}
    </div>
  );
}
