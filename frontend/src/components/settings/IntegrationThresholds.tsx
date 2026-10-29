'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Field, Input } from '@/components/ui/Field';
import { Skeleton } from '@/components/ui/Skeleton';
import { get, post, apiErrorBody, apiErrorMessage } from '@/lib/api';
import { useIsAdmin } from '@/stores/auth';
import { SaveBar, SettingsSection, useSaveStatus, useSectionForm } from './formKit';

export interface ThresholdRow {
  id: string;
  integration: string;
  integration_label: string;
  key: string;
  label: string;
  unit: string;
  default: number;
  value: number;
  min: number;
  max: number | null;
  zero_disables: boolean;
}

type Values = Record<string, string>;

/** Rows grouped by integration, in the order the server sent them. */
export function groupThresholds(rows: ThresholdRow[]): [string, ThresholdRow[]][] {
  const groups = new Map<string, ThresholdRow[]>();
  for (const r of rows) groups.set(r.integration_label, [...(groups.get(r.integration_label) ?? []), r]);
  return [...groups.entries()];
}

/** The numbers to save, or the ids of fields that are not a number in range. */
export function thresholdPayload(rows: ThresholdRow[], values: Values): { values: Record<string, number> } | { invalid: string[] } {
  const out: Record<string, number> = {};
  const invalid: string[] = [];
  for (const r of rows) {
    const n = Number((values[r.id] ?? '').trim());
    if ((values[r.id] ?? '').trim() === '' || !Number.isFinite(n) || n < r.min || (r.max !== null && n > r.max)) {
      invalid.push(r.id);
    } else {
      out[r.id] = n;
    }
  }
  return invalid.length ? { invalid } : { values: out };
}

/**
 * Settings -> Monitoring: the limits integration alerts compare against
 * (e.g. Proxmox node CPU, pool usage, certificate expiry). Shows the
 * integrations this tenant has configured; values equal to the default are
 * not stored, so a changed default reaches every tenant that kept it.
 */
export function IntegrationThresholds() {
  const qc = useQueryClient();
  const isAdmin = useIsAdmin();
  const query = useQuery({
    queryKey: ['integration-thresholds'],
    queryFn: () => get<ThresholdRow[]>('/settings/integration-thresholds'),
  });
  const rows = query.data ?? [];
  const form = useSectionForm<ThresholdRow[], Values>(
    query.data, query.dataUpdatedAt,
    (data) => Object.fromEntries(data.map((r) => [r.id, String(r.value)])),
  );
  const save = useSaveStatus(form.dirty);
  const values = form.value ?? {};
  const payload = thresholdPayload(rows, values);
  const invalid = new Set('invalid' in payload ? payload.invalid : []);

  async function onSave() {
    if ('invalid' in payload) {
      save.fail('Some values are not numbers in range');
      return;
    }
    save.start();
    try {
      await post('/settings/integration-thresholds/save', payload);
      form.markSaved();
      await qc.invalidateQueries({ queryKey: ['integration-thresholds'] });
      save.succeed();
    } catch (e) {
      const refused = apiErrorBody(e)?.refused;
      save.fail(Array.isArray(refused) ? `Out of range: ${refused.join(', ')}` : apiErrorMessage(e, 'Could not save'));
    }
  }

  return (
    <SettingsSection
      id="integration-alerts"
      title="Integration alert limits"
      description="When an integration's data crosses one of these limits for two collections in a row, an incident opens. Shown for the integrations you have configured."
    >
      {query.isLoading ? (
        <div className="space-y-2" aria-busy="true"><Skeleton className="h-9 w-full" /><Skeleton className="h-9 w-2/3" /></div>
      ) : rows.length === 0 ? (
        <p className="text-ui text-fg-3">None of your integrations has adjustable limits.</p>
      ) : (
        <div className="space-y-5">
          {groupThresholds(rows).map(([label, group]) => (
            <fieldset key={label}>
              <legend className="mb-2 text-ui font-medium text-fg">{label}</legend>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {group.map((r) => (
                  <Field
                    key={r.id}
                    label={`${r.label}${r.unit ? ` (${r.unit})` : ''}`}
                    hint={`Default ${r.default}${r.zero_disables ? ' · 0 turns the check off' : ''}`}
                    error={invalid.has(r.id) ? `Between ${r.min} and ${r.max ?? '∞'}` : undefined}
                  >
                    <Input
                      type="number"
                      inputMode="decimal"
                      className="num max-w-[180px]"
                      value={values[r.id] ?? ''}
                      min={r.min}
                      max={r.max ?? undefined}
                      disabled={!isAdmin}
                      onChange={(e) => form.set(r.id, e.target.value)}
                    />
                  </Field>
                ))}
              </div>
            </fieldset>
          ))}
          {isAdmin && <SaveBar status={save.status} onSave={onSave} onDiscard={form.discard} sticky={false} />}
        </div>
      )}
    </SettingsSection>
  );
}
