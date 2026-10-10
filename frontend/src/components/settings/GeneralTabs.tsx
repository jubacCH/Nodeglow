'use client';

import { Field, Input, Select, Switch, Textarea } from '@/components/ui/Field';
import { SaveBar, SettingsSection, type SaveStatus } from './formKit';
import type { GeneralForm } from './settingsForm';

const TIMEZONES = [
  'UTC',
  'Europe/Zurich', 'Europe/Berlin', 'Europe/Vienna', 'Europe/London',
  'Europe/Paris', 'Europe/Rome', 'Europe/Madrid', 'Europe/Amsterdam',
  'Europe/Brussels', 'Europe/Stockholm', 'Europe/Oslo', 'Europe/Helsinki',
  'Europe/Warsaw', 'Europe/Prague', 'Europe/Budapest', 'Europe/Bucharest',
  'Europe/Athens', 'Europe/Istanbul', 'Europe/Moscow',
  'US/Eastern', 'US/Central', 'US/Mountain', 'US/Pacific', 'US/Alaska', 'US/Hawaii',
  'Canada/Eastern', 'Canada/Central', 'Canada/Pacific',
  'America/Sao_Paulo', 'America/Buenos_Aires', 'America/Mexico_City',
  'Asia/Tokyo', 'Asia/Shanghai', 'Asia/Hong_Kong', 'Asia/Singapore',
  'Asia/Seoul', 'Asia/Kolkata', 'Asia/Dubai', 'Asia/Bangkok',
  'Australia/Sydney', 'Australia/Melbourne', 'Australia/Perth',
  'Pacific/Auckland', 'Africa/Cairo', 'Africa/Johannesburg',
];

interface GeneralTabProps {
  form: GeneralForm;
  set: <K extends keyof GeneralForm>(key: K, v: GeneralForm[K]) => void;
  status: SaveStatus;
  onSave: () => void;
  onDiscard: () => void;
}

const SHARED_NOTE = 'System and Monitoring are saved together.';

export function SystemTab({ form, set, status, onSave, onDiscard }: GeneralTabProps) {
  return (
    <div className="space-y-4">
      <SettingsSection id="general" title="General" description="Name and network address of this Nodeglow installation.">
        <div className="grid max-w-xl gap-4">
          <Field label="Instance name">
            <Input value={form.siteName} onChange={(e) => set('siteName', e.target.value)} placeholder="Nodeglow" />
          </Field>
          <Field
            label="Agent server address"
            hint="The address agents call back on. Leave empty to use the address you reached this page on. Set it when agents must reach the server on a different name than you do."
          >
            <Input
              inputMode="url"
              value={form.agentServerUrl}
              onChange={(e) => set('agentServerUrl', e.target.value)}
              placeholder="https://nodeglow.example.com"
            />
          </Field>
          <Field label="Timezone" hint="Used for schedules and timestamps in notifications.">
            <Select value={form.timezone} onChange={(e) => set('timezone', e.target.value)}>
              {TIMEZONES.map((tz) => <option key={tz} value={tz}>{tz}</option>)}
              {/* Show the current value even if it is not in the list */}
              {form.timezone && !TIMEZONES.includes(form.timezone) && (
                <option value={form.timezone}>{form.timezone}</option>
              )}
            </Select>
          </Field>
        </div>
      </SettingsSection>
      <SaveBar status={status} onSave={onSave} onDiscard={onDiscard} note={SHARED_NOTE} />
    </div>
  );
}

function NumberField({
  label, hint, value, onChange, min, max, step, placeholder,
}: {
  label: string; hint?: string; value: string; onChange: (v: string) => void;
  min?: number; max?: number; step?: number | string; placeholder?: string;
}) {
  return (
    <Field label={label} hint={hint}>
      <Input
        type="number"
        inputMode="decimal"
        className="num max-w-[180px]"
        value={value}
        min={min}
        max={max}
        step={step}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
    </Field>
  );
}

export function MonitoringTab({ form, set, status, onSave, onDiscard }: GeneralTabProps) {
  return (
    <div className="space-y-4">
      <SettingsSection id="checks" title="Checks" description="How often hosts and integrations are polled.">
        <div className="grid gap-4 sm:grid-cols-3">
          <NumberField label="Check interval (s)" value={form.pingInterval} onChange={(v) => set('pingInterval', v)} min={10} max={3600} />
          <NumberField label="Timeout (ms)" value={form.latencyThreshold} onChange={(v) => set('latencyThreshold', v)} placeholder="e.g. 5000" />
          <NumberField label="Integration interval (s)" value={form.proxmoxInterval} onChange={(v) => set('proxmoxInterval', v)} min={10} max={3600} />
        </div>
      </SettingsSection>

      <SettingsSection id="retention" title="Data retention" description="Older data is pruned nightly.">
        <div className="grid gap-4 sm:grid-cols-2">
          <NumberField label="Ping history (days)" value={form.pingRetention} onChange={(v) => set('pingRetention', v)} min={1} max={365} />
          <NumberField label="Integration snapshots (days)" value={form.integrationRetention} onChange={(v) => set('integrationRetention', v)} min={1} max={90} />
          <NumberField label="Proxmox history (days)" value={form.proxmoxRetention} onChange={(v) => set('proxmoxRetention', v)} min={1} max={90} />
          <NumberField
            label="Incident events (days)"
            hint="The newest event per incident is always kept. 0 = keep forever."
            value={form.incidentEventRetention}
            onChange={(v) => set('incidentEventRetention', v)}
            min={0}
            max={365}
          />
        </div>
      </SettingsSection>

      <SettingsSection id="syslog" title="Syslog">
        <Switch
          checked={form.syslogAllowlist}
          onChange={(v) => set('syslogAllowlist', v)}
          label="Host allowlist"
          description="Only accept syslog from IPs that match a host in your Hosts list."
        />
      </SettingsSection>

      <SettingsSection
        id="predictor"
        title="Predictive correlation"
        description={<>Threshold and blacklist for the learned-precursor predictor. Tighten these if you see false-positive &ldquo;Predicted: Host Down&rdquo; incidents from periodic noise such as DHCP renewals or NTP sync.</>}
      >
        <div className="mb-4 grid gap-4 sm:grid-cols-2">
          <NumberField label="Min. confidence (0.0 – 1.0)" value={form.predictorMinConfidence} onChange={(v) => set('predictorMinConfidence', v)} min={0} max={1} step="0.01" />
          <NumberField label="Min. historical observations" value={form.predictorMinOccurrences} onChange={(v) => set('predictorMinOccurrences', v)} min={1} step={1} />
        </div>
        <Field
          label="Template blacklist"
          hint="One regex per line, case-insensitive. Matching templates never become precursors and are removed on the next intelligence cycle."
        >
          <Textarea
            rows={6}
            spellCheck={false}
            className="font-mono text-meta"
            value={form.predictorBlacklistText}
            onChange={(e) => set('predictorBlacklistText', e.target.value)}
          />
        </Field>
      </SettingsSection>

      <SaveBar status={status} onSave={onSave} onDiscard={onDiscard} note={SHARED_NOTE} />
    </div>
  );
}
