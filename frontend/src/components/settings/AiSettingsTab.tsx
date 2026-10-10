'use client';

import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Plug, Send } from 'lucide-react';
import { BigNumber } from '@/components/ui/BigNumber';
import { Button } from '@/components/ui/Button';
import { Checkbox, Field, Input, Select } from '@/components/ui/Field';
import { QueryState } from '@/components/ui/QueryState';
import { Skeleton } from '@/components/ui/Skeleton';
import { SegmentedControl } from '@/components/ui/Tabs';
import { api, apiErrorBody, apiErrorMessage, get, post } from '@/lib/api';
import { useToastStore } from '@/stores/toast';
import { AI_STATUS_KEY } from '@/hooks/queries/useAiStatus';
import { ENTERPRISE_NOTES, hasAnyAiFeature, hasFeature, useFeatures } from '@/hooks/queries/useFeatures';
import { describeDestination, type AiProvider as Provider } from '@/lib/ai';
import { Code, Notice, SaveBar, SettingsSection, useSaveStatus, useSectionForm } from './formKit';

/* ---------- Types ---------- */

export interface AiConfig {
  ai_enabled: boolean;
  ai_enabled_by: string;
  ai_enabled_at: string;
  ai_provider: Provider;
  ai_anthropic_model: string;
  ai_anthropic_default_model: string;
  claude_has_key: boolean;
  ai_openai_base_url: string;
  ai_openai_has_key: boolean;
  ai_openai_model: string;
  ai_openai_api_version: string;
  ai_redact_enabled: boolean;
  ai_redact_hostnames: boolean;
  ai_restore_placeholders: boolean;
  configured: boolean;
  missing: string[];
  provider_label: string;
  destination: string;
  daily_ai_summary_enabled: boolean;
  daily_ai_summary_hour: string;
  daily_ai_summary_channels: string;
}

interface AiUsageBucket {
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  calls: number;
  month?: string;
}

const ALL_CHANNELS = ['telegram', 'discord', 'webhook', 'email', 'teams', 'slack', 'ntfy'] as const;

interface AiForm {
  enabled: boolean;
  provider: Provider;
  claudeKey: string;
  anthropicModel: string;
  baseUrl: string;
  openaiKey: string;
  clearOpenaiKey: boolean;
  openaiModel: string;
  apiVersion: string;
  redact: boolean;
  redactHosts: boolean;
  restore: boolean;
  dailyEnabled: boolean;
  dailyHour: string;
  /** Channel names in ALL_CHANNELS order. */
  dailyChannels: string[];
}

function formFromConfig(cfg: AiConfig): AiForm {
  const channels = new Set((cfg.daily_ai_summary_channels || ALL_CHANNELS.join(',')).split(',').filter(Boolean));
  return {
    enabled: cfg.ai_enabled,
    provider: cfg.ai_provider,
    claudeKey: '',
    anthropicModel: cfg.ai_anthropic_model,
    baseUrl: cfg.ai_openai_base_url,
    openaiKey: '',
    clearOpenaiKey: false,
    openaiModel: cfg.ai_openai_model,
    apiVersion: cfg.ai_openai_api_version,
    redact: cfg.ai_redact_enabled,
    redactHosts: cfg.ai_redact_hostnames,
    restore: cfg.ai_restore_placeholders,
    dailyEnabled: cfg.daily_ai_summary_enabled,
    dailyHour: cfg.daily_ai_summary_hour || '8',
    dailyChannels: [...ALL_CHANNELS.filter((c) => channels.has(c)), ...[...channels].filter((c) => !(ALL_CHANNELS as readonly string[]).includes(c))],
  };
}

function providerParams(f: AiForm): URLSearchParams {
  const p = new URLSearchParams();
  p.set('ai_provider', f.provider);
  if (f.provider === 'anthropic') {
    if (f.claudeKey.trim()) p.set('claude_api_key', f.claudeKey.trim());
    p.set('ai_anthropic_model', f.anthropicModel.trim());
  } else {
    p.set('ai_openai_base_url', f.baseUrl.trim());
    p.set('ai_openai_model', f.openaiModel.trim());
    p.set('ai_openai_api_version', f.apiVersion.trim());
    if (f.clearOpenaiKey) p.set('clear_openai_api_key', '1');
    else if (f.openaiKey.trim()) p.set('ai_openai_api_key', f.openaiKey.trim());
  }
  return p;
}

/* ---------- Usage card ---------- */

function AiUsageCard() {
  const usage = useQuery<{ monthly: AiUsageBucket; total: AiUsageBucket }>({
    queryKey: ['ai-usage'],
    queryFn: () => get('/settings/ai/usage'),
  });
  const fmt = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}K` : String(n));

  return (
    <SettingsSection id="ai-usage" title="Token usage" description="Cost is estimated for Anthropic only; other providers bill separately.">
      <QueryState
        query={usage}
        compact
        loading={<div className="grid grid-cols-2 gap-4"><Skeleton className="h-20" /><Skeleton className="h-20" /></div>}
      >
        {(data) => (
          <div className="grid gap-4 sm:grid-cols-2">
            {([['This month', data.monthly], ['All time', data.total]] as const).map(([label, b]) => (
              <div key={label} className="rounded-ctl border border-border bg-surface-2 p-4">
                <p className="mb-2 text-meta text-fg-2">{label}</p>
                <BigNumber size="sm" value={fmt(b.input_tokens + b.output_tokens)} unit="tokens" label={`${b.calls} calls · $${b.cost_usd.toFixed(4)}`} />
              </div>
            ))}
          </div>
        )}
      </QueryState>
    </SettingsSection>
  );
}

/* ---------- Tab ---------- */

export function AiSettingsTab() {
  const { data: features } = useFeatures();
  // Community edition: none of the AI features is installed, so there is
  // nothing to configure — the tab shows one calm note instead.
  const noAiFeatures = !!features && !hasAnyAiFeature(features);
  const dailyInstalled = hasFeature(features, 'ai_daily_summary');
  const toast = useToastStore();
  const qc = useQueryClient();
  const cfgQuery = useQuery<AiConfig>({
    queryKey: ['ai-config'],
    queryFn: () => get('/settings/ai/config'),
    enabled: !noAiFeatures,
  });
  const cfg = cfgQuery.data;
  const form = useSectionForm(cfg, cfgQuery.dataUpdatedAt, formFromConfig);
  const save = useSaveStatus(form.dirty);
  const [testing, setTesting] = useState(false);
  const [testingSummary, setTestingSummary] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);

  const f = form.value;

  async function doSave(): Promise<boolean> {
    if (!f) return false;
    save.start();
    try {
      const p = providerParams(f);
      p.set('ai_enabled', f.enabled ? '1' : '0');
      p.set('ai_redact_enabled', f.redact ? '1' : '0');
      p.set('ai_redact_hostnames', f.redactHosts ? '1' : '0');
      p.set('ai_restore_placeholders', f.restore ? '1' : '0');
      p.set('daily_ai_summary_enabled', f.dailyEnabled ? 'on' : '0');
      p.set('daily_ai_summary_hour', f.dailyHour);
      p.set('daily_ai_summary_channels', f.dailyChannels.join(','));
      await api('/settings/ai/save', { method: 'POST', body: p });
      form.markSaved();
      save.succeed();
      qc.invalidateQueries({ queryKey: ['ai-config'] });
      qc.invalidateQueries({ queryKey: AI_STATUS_KEY });
      qc.invalidateQueries({ queryKey: ['settings'] });
      toast.show('AI settings saved', 'success');
      return true;
    } catch (e) {
      const msg = apiErrorMessage(e, 'Failed to save AI settings');
      save.fail(msg);
      toast.show(msg, 'error');
      return false;
    }
  }

  async function testConnection() {
    if (!f) return;
    setTesting(true);
    setTestResult(null);
    try {
      const res = await api<{ ok: boolean; message: string }>('/settings/ai/test-connection', {
        method: 'POST', body: providerParams(f),
      });
      setTestResult(res);
    } catch (e) {
      const body = apiErrorBody(e);
      setTestResult({ ok: false, message: (typeof body?.message === 'string' && body.message) || apiErrorMessage(e, 'Connection test failed') });
    } finally {
      setTesting(false);
    }
  }

  async function testSummary() {
    setTestingSummary(true);
    try {
      // Saves first, as before, so the summary uses the values on screen.
      if (!(await doSave())) return;
      const res = await post<{ ok: boolean; message: string }>('/settings/ai/test-summary');
      toast.show(res.message || 'Test summary sent', 'success');
    } catch (e) {
      const body = apiErrorBody(e);
      toast.show((typeof body?.message === 'string' && body.message) || 'Test summary failed — check server logs', 'error');
    } finally {
      setTestingSummary(false);
    }
  }

  if (noAiFeatures) {
    return (
      <SettingsSection id="ai-optin" title="AI features">
        <Notice tone="info">
          <span data-testid="ai-enterprise-note">{ENTERPRISE_NOTES.ai}</span>
        </Notice>
      </SettingsSection>
    );
  }

  return (
    <QueryState
      query={cfgQuery}
      isEmpty={() => !f}
      empty={<div className="space-y-4" aria-busy="true" aria-label="Loading"><Skeleton className="h-40 w-full" /><Skeleton className="h-56 w-full" /></div>}
      loading={<div className="space-y-4" aria-busy="true" aria-label="Loading"><Skeleton className="h-40 w-full" /><Skeleton className="h-56 w-full" /></div>}
    >
      {() => f && renderForm(f)}
    </QueryState>
  );

  // A render helper, not a component: a nested component type would remount
  // (and drop input focus) on every keystroke.
  function renderForm(f: AiForm) {
    const destination = describeDestination(f.provider, f.baseUrl, f.apiVersion);
    const keyMissing = f.provider === 'anthropic' ? !cfg?.claude_has_key && !f.claudeKey.trim() : false;
    const azure = !!f.apiVersion.trim();

    return (
      <div className="space-y-4">
        <SettingsSection
          id="ai-optin"
          title="AI features"
          description="Glow (chat), automatic postmortems and the daily AI summary. Off by default; nothing is sent to an AI provider unless you switch this on."
        >
          <Checkbox
            label="Enable AI features for this installation"
            checked={f.enabled}
            onChange={(e) => form.set('enabled', e.target.checked)}
            data-testid="ai-enabled"
          />
          {cfg?.ai_enabled && cfg.ai_enabled_by && (
            <p className="mt-2 text-meta text-fg-3">
              Enabled by {cfg.ai_enabled_by}{cfg.ai_enabled_at ? ` on ${new Date(cfg.ai_enabled_at + 'Z').toLocaleString()}` : ''}.
            </p>
          )}
          <div className="mt-4 space-y-1 rounded-ctl border border-border-2 bg-surface-2 px-3 py-2 text-meta text-fg-2" data-testid="ai-data-notice">
            <p className="font-medium text-fg">What is sent, and to whom</p>
            <p>
              When a feature runs, Nodeglow sends to <strong className="text-fg">{destination}</strong>: host and incident counts, names of
              offline hosts and unhealthy integrations, incident titles and timelines, a few example syslog messages
              per error pattern, and in Glow your question plus the last 10 chat messages.
            </p>
            <p>
              {f.redact
                ? 'Before sending, IP and MAC addresses, e-mail addresses, usernames in common log formats and secrets (passwords, tokens, keys) are replaced by placeholders such as <IP_1>. Redaction is pattern-based and best effort, not a guarantee.'
                : 'Redaction is OFF: log lines are sent as they are, including IP addresses and usernames.'}
            </p>
          </div>
        </SettingsSection>

        <SettingsSection
          id="ai-provider"
          title="Provider"
          description="Anthropic, or any endpoint that speaks the OpenAI Chat Completions API: Azure OpenAI (e.g. Switzerland North), Ollama, vLLM, LM Studio."
        >
          <div className="space-y-4">
            <SegmentedControl<Provider>
              label="AI provider"
              value={f.provider}
              onChange={(v) => { form.set('provider', v); setTestResult(null); }}
              options={[
                { value: 'anthropic', label: 'Anthropic (Claude)' },
                { value: 'openai_compatible', label: 'OpenAI-compatible / Azure / local' },
              ]}
              className="max-w-full flex-wrap"
            />

            {f.provider === 'anthropic' ? (
              <div className="grid max-w-xl gap-4">
                <Field
                  label={<>API key {cfg?.claude_has_key ? <span className="ml-1.5 font-normal text-ok">· configured</span> : <span className="ml-1.5 font-normal text-fg-3">· no key configured</span>}</>}
                  hint={<>From <a href="https://console.anthropic.com" target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">console.anthropic.com</a>. Stored encrypted.</>}
                >
                  <Input
                    id="ai-claude-key"
                    type="password"
                    autoComplete="off"
                    value={f.claudeKey}
                    onChange={(e) => form.set('claudeKey', e.target.value)}
                    placeholder={cfg?.claude_has_key ? '•••••••• (keep current key)' : 'sk-ant-...'}
                  />
                </Field>
                <Field label="Model">
                  <Input id="ai-anthropic-model" className="font-mono" value={f.anthropicModel} onChange={(e) => form.set('anthropicModel', e.target.value)} placeholder={cfg?.ai_anthropic_default_model} />
                </Field>
              </div>
            ) : (
              <div className="grid max-w-xl gap-4">
                <Field
                  label="Base URL"
                  hint={<>Ollama <Code>http://host:11434/v1</Code> · LM Studio <Code>http://host:1234/v1</Code> · vLLM <Code>http://host:8000/v1</Code> · Azure <Code>https://&lt;resource&gt;.openai.azure.com</Code>. Local and private addresses are allowed; <Code>localhost</Code> is the Nodeglow container itself.</>}
                >
                  <Input id="ai-base-url" className="font-mono" inputMode="url" value={f.baseUrl} onChange={(e) => form.set('baseUrl', e.target.value)} placeholder="http://ollama.local:11434/v1" />
                </Field>
                <Field label={`Model${azure ? ' (Azure deployment name)' : ''}`}>
                  <Input id="ai-openai-model" className="font-mono" value={f.openaiModel} onChange={(e) => form.set('openaiModel', e.target.value)} placeholder={azure ? 'gpt-4o-mini-chn' : 'llama3.1:8b'} />
                </Field>
                <Field label="Azure API version" hint="Azure OpenAI only; leave empty for other servers.">
                  <Input id="ai-api-version" className="font-mono" value={f.apiVersion} onChange={(e) => form.set('apiVersion', e.target.value)} placeholder="2024-10-21" />
                </Field>
                <div>
                  <Field label={`API key${azure ? '' : ' (optional for local servers)'}`}>
                    <Input
                      id="ai-openai-key"
                      type="password"
                      autoComplete="off"
                      value={f.openaiKey}
                      onChange={(e) => form.patch({ openaiKey: e.target.value, clearOpenaiKey: false })}
                      placeholder={cfg?.ai_openai_has_key ? '•••••••• (keep current key)' : ''}
                      disabled={f.clearOpenaiKey}
                    />
                  </Field>
                  {cfg?.ai_openai_has_key && (
                    <Checkbox className="mt-2" label="Remove stored key" checked={f.clearOpenaiKey} onChange={(e) => form.set('clearOpenaiKey', e.target.checked)} />
                  )}
                </div>
              </div>
            )}

            <div className="flex flex-wrap items-center gap-3">
              <Button size="sm" variant="secondary" onClick={testConnection} loading={testing} disabled={testing || keyMissing} data-testid="ai-test-connection">
                {!testing && <Plug size={13} aria-hidden="true" />}
                {testing ? 'Testing…' : 'Test connection'}
              </Button>
              <span className="text-meta text-fg-3">Sends a fixed “Reply with OK” prompt, no infrastructure data. Uses the values above without saving.</span>
            </div>
            {testResult && <Notice tone={testResult.ok ? 'ok' : 'down'}>{testResult.message}</Notice>}
          </div>
        </SettingsSection>

        <SettingsSection
          id="ai-redaction"
          title="Redaction"
          description="Personal data is replaced by stable placeholders before anything leaves Nodeglow; the same value always gets the same placeholder within one request, so the model can still correlate."
        >
          <div className="space-y-3">
            <Checkbox
              label="Redact personal data and secrets"
              description="IPv4/IPv6, MAC, e-mail, usernames (sshd, PAM, sudo, Windows events, nginx), Windows SIDs and workstation names, passwords/tokens/API keys."
              checked={f.redact}
              onChange={(e) => form.set('redact', e.target.checked)}
            />
            <Checkbox
              label="Also redact hostnames and FQDNs"
              description="Includes the names of your monitored hosts and integrations. Answers become less specific."
              checked={f.redactHosts}
              disabled={!f.redact}
              onChange={(e) => form.set('redactHosts', e.target.checked)}
            />
            <Checkbox
              label="Show real values in answers"
              description="Placeholders are mapped back inside Nodeglow only; the mapping is never sent. Secrets always stay masked."
              checked={f.restore}
              disabled={!f.redact}
              onChange={(e) => form.set('restore', e.target.checked)}
            />
          </div>
        </SettingsSection>

        {dailyInstalled && <SettingsSection
          id="ai-daily"
          title="Daily AI summary"
          description="A daily AI-generated briefing with incidents, root cause analysis and resolution suggestions, sent via the selected notification channels."
        >
          <div className="space-y-4">
            <Checkbox label="Enable daily AI summary" checked={f.dailyEnabled} onChange={(e) => form.set('dailyEnabled', e.target.checked)} />
            <Field label="Send at (UTC)" className="max-w-[200px]">
              <Select id="ai-daily-hour" value={f.dailyHour} onChange={(e) => form.set('dailyHour', e.target.value)} disabled={!f.dailyEnabled}>
                {Array.from({ length: 24 }, (_, i) => (
                  <option key={i} value={String(i)}>{String(i).padStart(2, '0')}:00</option>
                ))}
              </Select>
            </Field>
            <fieldset>
              <legend className="ng-label">Channels</legend>
              <div className="mt-1 flex flex-wrap gap-x-5 gap-y-2">
                {ALL_CHANNELS.map((ch) => (
                  <Checkbox
                    key={ch}
                    label={<span className="capitalize">{ch}</span>}
                    checked={f.dailyChannels.includes(ch)}
                    disabled={!f.dailyEnabled}
                    onChange={(e) => {
                      const next = new Set(f.dailyChannels);
                      if (e.target.checked) next.add(ch); else next.delete(ch);
                      form.set('dailyChannels', ALL_CHANNELS.filter((c) => next.has(c)));
                    }}
                  />
                ))}
              </div>
            </fieldset>
            {!cfg?.ai_enabled && <Notice tone="info">Runs only while AI features are enabled and saved (above).</Notice>}
          </div>
        </SettingsSection>}

        <AiUsageCard />

        <SaveBar
          status={save.status}
          onSave={() => { void doSave(); }}
          onDiscard={form.discard}
          label="Save AI settings"
          extra={dailyInstalled && (
            <Button
              size="sm"
              variant="ghost"
              loading={testingSummary}
              disabled={testingSummary || !cfg?.ai_enabled}
              title={!cfg?.ai_enabled ? 'Enable and save AI features first' : undefined}
              onClick={testSummary}
            >
              {!testingSummary && <Send size={13} aria-hidden="true" />}
              {testingSummary ? 'Generating…' : 'Send test summary'}
            </Button>
          )}
        />
      </div>
    );
  }
}
