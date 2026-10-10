'use client';

import { useEffect, useId, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Play } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Checkbox, Field, Input, Select, Textarea } from '@/components/ui/Field';
import { Modal } from '@/components/ui/Modal';
import { Skeleton } from '@/components/ui/Skeleton';
import { api, apiErrorMessage, get, post } from '@/lib/api';
import {
  DEFAULT_RULE_FORM, NOTIFY_CHANNELS, OPERATORS, SEVERITIES, needsThreshold, ruleFormData, ruleToForm, validateRule,
  type RuleFormState,
} from '@/lib/rules';
import type { AlertRule } from '@/types';

interface SourceOption {
  type: string;
  label: string;
  instances: { id: number; name: string }[];
}

interface FieldOption {
  path: string;
  value: unknown;
  type: string;
}

interface TestResult {
  current_value: unknown;
  would_trigger: boolean;
  detail: string;
}

function display(v: unknown) {
  return typeof v === 'object' ? JSON.stringify(v) : String(v);
}

/** Create / edit an alert rule, with a dry-run against the latest data. */
export function RuleEditor({ open, rule, onClose, onSaved }: {
  open: boolean;
  /** null = new rule. */
  rule: AlertRule | null;
  onClose: () => void;
  onSaved: (created: boolean) => void;
}) {
  const id = useId();
  const [form, setForm] = useState<RuleFormState>(DEFAULT_RULE_FORM);
  const [submitted, setSubmitted] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [testError, setTestError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setForm(rule ? ruleToForm(rule) : DEFAULT_RULE_FORM);
    setSubmitted(false);
    setSaveError(null);
    setTestResult(null);
    setTestError(null);
  }, [open, rule]);

  const sources = useQuery({
    queryKey: ['rule-sources'],
    queryFn: () => get<SourceOption[]>('/api/rules/sources'),
    enabled: open,
    staleTime: 60_000,
  });
  const fields = useQuery({
    queryKey: ['rule-fields', form.source_type, form.source_id],
    queryFn: () => {
      const p = new URLSearchParams({ source_type: form.source_type });
      if (form.source_id) p.set('source_id', form.source_id);
      return get<FieldOption[]>(`/api/rules/fields?${p}`);
    },
    enabled: open && !!form.source_type,
    staleTime: 30_000,
  });

  const errors = validateRule(form);
  const shown = submitted ? errors : {};
  const selectedSource = sources.data?.find((s) => s.type === form.source_type);
  const fieldOptions = fields.data ?? [];
  const selectedField = fieldOptions.find((f) => f.path === form.field_path);
  const thresholdOn = needsThreshold(form.operator);

  function update<K extends keyof RuleFormState>(key: K, value: RuleFormState[K]) {
    setTestResult(null);
    setForm((prev) => {
      const next = { ...prev, [key]: value };
      // Dependent fields reset when the source changes.
      if (key === 'source_type') { next.source_id = ''; next.field_path = ''; }
      if (key === 'source_id') next.field_path = '';
      return next;
    });
  }

  const toggleChannel = (ch: string) =>
    update('notify_channels', form.notify_channels.includes(ch) ? form.notify_channels.filter((c) => c !== ch) : [...form.notify_channels, ch]);

  async function handleTest() {
    setTesting(true);
    setTestResult(null);
    setTestError(null);
    try {
      setTestResult(await post<TestResult>('/api/rules/test', {
        source_type: form.source_type,
        source_id: form.source_id || null,
        field_path: form.field_path,
        operator: form.operator,
        threshold: form.threshold,
      }));
    } catch (e) {
      setTestError(apiErrorMessage(e, 'The test could not run.'));
    } finally {
      setTesting(false);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitted(true);
    if (Object.keys(errors).length) return;
    setSaving(true);
    setSaveError(null);
    try {
      await api(rule ? `/rules/${rule.id}/edit` : '/rules/add', { method: 'POST', body: ruleFormData(form) });
      onSaved(!rule);
      onClose();
    } catch (err) {
      setSaveError(apiErrorMessage(err, 'Could not save the rule.'));
    } finally {
      setSaving(false);
    }
  }

  const formId = `${id}-form`;
  const canTest = !!form.source_type && !!form.field_path;

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="lg"
      title={rule ? 'Edit alert rule' : 'New alert rule'}
      description="Rules check integration data on every poll and open an incident when the condition holds."
      footer={
        <>
          <Button variant="ghost" onClick={handleTest} loading={testing} disabled={!canTest} className="mr-auto">
            {!testing && <Play size={14} aria-hidden="true" />} Test against current data
          </Button>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button type="submit" form={formId} loading={saving}>{rule ? 'Save changes' : 'Create rule'}</Button>
        </>
      }
    >
      <form id={formId} onSubmit={handleSubmit} noValidate className="space-y-4">
        <Field label="Name" required error={shown.name}>
          <Input type="text" placeholder="e.g. High CPU on a hypervisor" value={form.name} onChange={(e) => update('name', e.target.value)} />
        </Field>

        <div className="grid grid-cols-1 gap-3 min-[480px]:grid-cols-2">
          <Field label="Source" required error={shown.source_type} hint={sources.isError ? 'Sources could not be loaded.' : undefined}>
            <Select value={form.source_type} onChange={(e) => update('source_type', e.target.value)} disabled={sources.isLoading}>
              <option value="">{sources.isLoading ? 'Loading…' : 'Select a source…'}</option>
              {sources.data?.map((s) => <option key={s.type} value={s.type}>{s.label}</option>)}
            </Select>
          </Field>
          <Field label="Instance" hint={selectedSource?.instances.length ? 'Empty = all instances' : undefined}>
            <Select value={form.source_id} onChange={(e) => update('source_id', e.target.value)} disabled={!selectedSource?.instances.length}>
              <option value="">{selectedSource?.instances.length ? 'All instances' : 'Not applicable'}</option>
              {selectedSource?.instances.map((inst) => <option key={inst.id} value={String(inst.id)}>{inst.name}</option>)}
            </Select>
          </Field>
        </div>

        {fields.isLoading && form.source_type ? (
          <div>
            <span className="ng-label">Field</span>
            <Skeleton className="h-9 w-full" />
          </div>
        ) : fieldOptions.length > 0 ? (
          <Field
            label="Field"
            required
            error={shown.field_path}
            hint={selectedField && selectedField.value != null ? <>Current value: <span className="font-mono text-fg-2">{display(selectedField.value)}</span></> : undefined}
          >
            <Select value={form.field_path} onChange={(e) => update('field_path', e.target.value)}>
              <option value="">Select a field…</option>
              {fieldOptions.map((f) => <option key={f.path} value={f.path}>{f.path} ({f.type})</option>)}
            </Select>
          </Field>
        ) : (
          <Field label="Field" required error={shown.field_path} hint={form.source_type ? 'No sample data yet — enter the field path by hand.' : undefined}>
            <Input type="text" className="font-mono" placeholder="e.g. cpu_pct or data.temperature" value={form.field_path} onChange={(e) => update('field_path', e.target.value)} />
          </Field>
        )}

        <div className="grid grid-cols-1 gap-3 min-[480px]:grid-cols-2">
          <Field label="Condition">
            <Select value={form.operator} onChange={(e) => update('operator', e.target.value)}>
              {OPERATORS.map((op) => <option key={op.key} value={op.key}>{op.label}</option>)}
            </Select>
          </Field>
          <Field label="Threshold" required={thresholdOn} error={shown.threshold} hint={thresholdOn ? undefined : 'Not used by this condition.'}>
            <Input
              type="text"
              placeholder={thresholdOn ? (form.operator === 'regex' ? 'e.g. ^error' : 'e.g. 90') : '—'}
              value={thresholdOn ? form.threshold : ''}
              onChange={(e) => update('threshold', e.target.value)}
              disabled={!thresholdOn}
            />
          </Field>
        </div>

        <div className="grid grid-cols-1 gap-3 min-[480px]:grid-cols-3">
          <Field label="Severity">
            <Select value={form.severity} onChange={(e) => update('severity', e.target.value)}>
              {SEVERITIES.map((s) => <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>)}
            </Select>
          </Field>
          <Field label="Cooldown (min)" error={shown.cooldown_minutes} hint="Wait before alerting again.">
            <Input type="number" inputMode="numeric" min={1} value={form.cooldown_minutes} onChange={(e) => update('cooldown_minutes', e.target.value)} />
          </Field>
          <Field label="Consecutive matches" error={shown.required_consecutive} hint="Polls in a row before alerting.">
            <Input type="number" inputMode="numeric" min={1} max={10} value={form.required_consecutive} onChange={(e) => update('required_consecutive', e.target.value)} />
          </Field>
        </div>

        <fieldset>
          <legend className="ng-label">Notify channels</legend>
          <p className="mb-2 text-meta text-fg-3">None selected = all configured channels.</p>
          <div className="grid grid-cols-2 gap-2 min-[480px]:grid-cols-4">
            {NOTIFY_CHANNELS.map((ch) => (
              <Checkbox key={ch} checked={form.notify_channels.includes(ch)} onChange={() => toggleChannel(ch)} label={ch} />
            ))}
          </div>
        </fieldset>

        <Field label="Message template" hint="Optional. Placeholders: {name}, {field}, {value}, {threshold}.">
          <Textarea
            rows={2}
            className="resize-y font-mono"
            placeholder="{name}: {field} is {value} (threshold: {threshold})"
            value={form.message_template}
            onChange={(e) => update('message_template', e.target.value)}
          />
        </Field>

        {testResult && (
          <div role="status" className={testResult.would_trigger
            ? 'rounded-ctl border border-warning/30 bg-warning-soft px-3 py-2.5'
            : 'rounded-ctl border border-border-2 bg-surface-2 px-3 py-2.5'}>
            <p className={testResult.would_trigger ? 'text-ui font-medium text-warning' : 'text-ui font-medium text-fg'}>
              {testResult.would_trigger ? 'Would trigger now' : 'Would not trigger now'}
            </p>
            <p className="mt-0.5 font-mono text-meta text-fg-2 [overflow-wrap:anywhere]">{testResult.detail}</p>
            {testResult.current_value != null && (
              <p className="mt-0.5 text-meta text-fg-2">Current value: <span className="font-mono">{display(testResult.current_value)}</span></p>
            )}
          </div>
        )}
        {testError && <p role="alert" className="text-meta text-down">{testError}</p>}
        {submitted && Object.keys(errors).length > 0 && (
          <p role="alert" className="text-meta text-down">Fix the highlighted fields to save the rule.</p>
        )}
        {saveError && <p role="alert" className="rounded-ctl border border-down/30 bg-down-soft px-3 py-2 text-ui text-down">{saveError}</p>}
      </form>
    </Modal>
  );
}
