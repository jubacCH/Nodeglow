'use client';

import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Activity, Bell, Plug, Send, ShieldCheck, Sparkles } from 'lucide-react';
import { GlassCard } from '@/components/ui/GlassCard';
import { Button } from '@/components/ui/Button';
import { api, apiErrorBody, apiErrorMessage, get, post } from '@/lib/api';
import { useToastStore } from '@/stores/toast';
import { AI_STATUS_KEY } from '@/hooks/queries/useAiStatus';
import { describeDestination, type AiProvider as Provider } from '@/lib/ai';

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

const inputCls = 'ng-input max-w-sm';
const selectSmCls = 'w-full max-w-[180px] px-2 py-1.5 rounded-md bg-[var(--ng-surface)] border border-white/[0.06] text-xs text-slate-200 focus:outline-none focus:ring-2 focus:ring-sky-500/50 transition-colors [&>option]:text-[var(--ng-text-primary)]';
const checkboxCls = 'rounded border-white/20 bg-white/[0.04] text-sky-500 focus:ring-sky-500/50';
const ALL_CHANNELS = ['telegram', 'discord', 'webhook', 'email', 'teams', 'slack', 'ntfy'] as const;

/* ---------- Usage card ---------- */

function AiUsageCard() {
  const { data } = useQuery<{ monthly: AiUsageBucket; total: AiUsageBucket }>({
    queryKey: ['ai-usage'],
    queryFn: () => get('/settings/ai/usage'),
  });

  if (!data) return null;

  const fmt = (n: number) => n >= 1000 ? `${(n / 1000).toFixed(1)}K` : String(n);

  return (
    <GlassCard className="p-4">
      <h3 className="text-base font-semibold text-slate-200 mb-3 flex items-center gap-2">
        <Activity size={16} className="text-violet-400" />
        AI Token Usage
      </h3>
      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-lg bg-white/[0.03] border border-white/[0.06] p-3">
          <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">This Month</div>
          <div className="text-lg font-bold text-[var(--ng-text-primary)]">{fmt(data.monthly.input_tokens + data.monthly.output_tokens)}</div>
          <div className="text-[11px] text-slate-500">tokens &middot; {data.monthly.calls} calls</div>
          <div className="text-xs text-emerald-400 mt-1">${data.monthly.cost_usd.toFixed(4)}</div>
        </div>
        <div className="rounded-lg bg-white/[0.03] border border-white/[0.06] p-3">
          <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">All Time</div>
          <div className="text-lg font-bold text-[var(--ng-text-primary)]">{fmt(data.total.input_tokens + data.total.output_tokens)}</div>
          <div className="text-[11px] text-slate-500">tokens &middot; {data.total.calls} calls</div>
          <div className="text-xs text-emerald-400 mt-1">${data.total.cost_usd.toFixed(4)}</div>
        </div>
      </div>
      <p className="text-[11px] text-slate-500 mt-2">Cost is estimated for Anthropic only; other providers bill separately.</p>
    </GlassCard>
  );
}

/* ---------- Tab ---------- */

export function AiSettingsTab() {
  const toast = useToastStore();
  const qc = useQueryClient();
  const { data: cfg } = useQuery<AiConfig>({
    queryKey: ['ai-config'],
    queryFn: () => get('/settings/ai/config'),
  });

  const [enabled, setEnabled] = useState(false);
  const [provider, setProvider] = useState<Provider>('anthropic');
  const [claudeKey, setClaudeKey] = useState('');
  const [anthropicModel, setAnthropicModel] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [openaiKey, setOpenaiKey] = useState('');
  const [clearOpenaiKey, setClearOpenaiKey] = useState(false);
  const [openaiModel, setOpenaiModel] = useState('');
  const [apiVersion, setApiVersion] = useState('');
  const [redact, setRedact] = useState(true);
  const [redactHosts, setRedactHosts] = useState(false);
  const [restore, setRestore] = useState(true);
  const [dailyEnabled, setDailyEnabled] = useState(false);
  const [dailyHour, setDailyHour] = useState('8');
  const [dailyChannels, setDailyChannels] = useState<Set<string>>(new Set(ALL_CHANNELS));
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testingSummary, setTestingSummary] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);

  useEffect(() => {
    if (!cfg) return;
    setEnabled(cfg.ai_enabled);
    setProvider(cfg.ai_provider);
    setAnthropicModel(cfg.ai_anthropic_model);
    setBaseUrl(cfg.ai_openai_base_url);
    setOpenaiModel(cfg.ai_openai_model);
    setApiVersion(cfg.ai_openai_api_version);
    setRedact(cfg.ai_redact_enabled);
    setRedactHosts(cfg.ai_redact_hostnames);
    setRestore(cfg.ai_restore_placeholders);
    setDailyEnabled(cfg.daily_ai_summary_enabled);
    setDailyHour(cfg.daily_ai_summary_hour || '8');
    setDailyChannels(new Set((cfg.daily_ai_summary_channels || ALL_CHANNELS.join(',')).split(',').filter(Boolean)));
    setClaudeKey('');
    setOpenaiKey('');
    setClearOpenaiKey(false);
  }, [cfg]);

  function providerParams(): URLSearchParams {
    const p = new URLSearchParams();
    p.set('ai_provider', provider);
    if (provider === 'anthropic') {
      if (claudeKey.trim()) p.set('claude_api_key', claudeKey.trim());
      p.set('ai_anthropic_model', anthropicModel.trim());
    } else {
      p.set('ai_openai_base_url', baseUrl.trim());
      p.set('ai_openai_model', openaiModel.trim());
      p.set('ai_openai_api_version', apiVersion.trim());
      if (clearOpenaiKey) p.set('clear_openai_api_key', '1');
      else if (openaiKey.trim()) p.set('ai_openai_api_key', openaiKey.trim());
    }
    return p;
  }

  async function save(): Promise<boolean> {
    setSaving(true);
    try {
      const p = providerParams();
      p.set('ai_enabled', enabled ? '1' : '0');
      p.set('ai_redact_enabled', redact ? '1' : '0');
      p.set('ai_redact_hostnames', redactHosts ? '1' : '0');
      p.set('ai_restore_placeholders', restore ? '1' : '0');
      p.set('daily_ai_summary_enabled', dailyEnabled ? 'on' : '0');
      p.set('daily_ai_summary_hour', dailyHour);
      p.set('daily_ai_summary_channels', Array.from(dailyChannels).join(','));
      await api('/settings/ai/save', { method: 'POST', body: p });
      qc.invalidateQueries({ queryKey: ['ai-config'] });
      qc.invalidateQueries({ queryKey: AI_STATUS_KEY });
      qc.invalidateQueries({ queryKey: ['settings'] });
      toast.show('AI settings saved', 'success');
      return true;
    } catch (e) {
      toast.show(apiErrorMessage(e, 'Failed to save AI settings'), 'error');
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function testConnection() {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await api<{ ok: boolean; message: string }>('/settings/ai/test-connection', {
        method: 'POST', body: providerParams(),
      });
      setTestResult(res);
    } catch (e) {
      const body = apiErrorBody(e);
      setTestResult({ ok: false, message: (typeof body?.message === 'string' && body.message) || apiErrorMessage(e, 'Connection test failed') });
    } finally {
      setTesting(false);
    }
  }

  const destination = describeDestination(provider, baseUrl, apiVersion);
  const keyMissing = provider === 'anthropic' ? !cfg?.claude_has_key && !claudeKey.trim() : false;

  return (
    <div className="space-y-4">
      {/* Opt-in */}
      <GlassCard className="p-4">
        <h3 className="text-base font-semibold text-slate-200 mb-1 flex items-center gap-2">
          <Sparkles size={16} className="text-violet-400" />
          AI features
        </h3>
        <p className="text-xs text-slate-400 mb-4">
          Glow (chat), automatic postmortems and the daily AI summary. Off by default; nothing is sent to an AI provider unless you switch this on.
        </p>
        <label className="flex items-center gap-2 cursor-pointer mb-3">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
            className={checkboxCls}
            data-testid="ai-enabled"
          />
          <span className="text-sm text-[var(--ng-text-primary)]">Enable AI features for this installation</span>
        </label>
        {cfg?.ai_enabled && cfg.ai_enabled_by && (
          <p className="text-[11px] text-slate-500 mb-3">
            Enabled by {cfg.ai_enabled_by}{cfg.ai_enabled_at ? ` on ${new Date(cfg.ai_enabled_at + 'Z').toLocaleString()}` : ''}.
          </p>
        )}
        <div className="rounded-md border border-sky-500/20 bg-sky-500/5 px-3 py-2 text-xs text-[var(--ng-text-secondary)] space-y-1" data-testid="ai-data-notice">
          <p className="font-medium text-[var(--ng-text-primary)]">What is sent, and to whom</p>
          <p>
            When a feature runs, Nodeglow sends to <strong>{destination}</strong>: host and incident counts, names of
            offline hosts and unhealthy integrations, incident titles and timelines, a few example syslog messages
            per error pattern, and in Glow your question plus the last 10 chat messages.
          </p>
          <p>
            {redact
              ? 'Before sending, IP and MAC addresses, e-mail addresses, usernames in common log formats and secrets (passwords, tokens, keys) are replaced by placeholders such as <IP_1>. Redaction is pattern-based and best effort, not a guarantee.'
              : 'Redaction is OFF: log lines are sent as they are, including IP addresses and usernames.'}
          </p>
        </div>
      </GlassCard>

      {/* Provider */}
      <GlassCard className="p-4">
        <h3 className="text-base font-semibold text-slate-200 mb-1 flex items-center gap-2">
          <Plug size={16} className="text-violet-400" />
          Provider
        </h3>
        <p className="text-xs text-slate-400 mb-4">
          Anthropic, or any endpoint that speaks the OpenAI Chat Completions API: Azure OpenAI (e.g. Switzerland North), Ollama, vLLM, LM Studio.
        </p>
        <div className="space-y-3">
          <div className="flex flex-wrap gap-4" role="radiogroup" aria-label="AI provider">
            {([
              ['anthropic', 'Anthropic (Claude)'],
              ['openai_compatible', 'OpenAI-compatible / Azure / local'],
            ] as const).map(([value, label]) => (
              <label key={value} className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="ai-provider"
                  value={value}
                  checked={provider === value}
                  onChange={() => { setProvider(value); setTestResult(null); }}
                  className="border-white/20 bg-white/[0.04] text-sky-500 focus:ring-sky-500/50"
                />
                <span className="text-sm text-[var(--ng-text-primary)]">{label}</span>
              </label>
            ))}
          </div>

          {provider === 'anthropic' ? (
            <>
              <div>
                <label className="ng-label" htmlFor="ai-claude-key">API key</label>
                <div className="flex items-center gap-2">
                  <input
                    id="ai-claude-key"
                    type="password"
                    value={claudeKey}
                    onChange={(e) => setClaudeKey(e.target.value)}
                    className={inputCls}
                    placeholder={cfg?.claude_has_key ? '•••••••• (keep current key)' : 'sk-ant-...'}
                    autoComplete="off"
                  />
                  <span className={`text-xs whitespace-nowrap ${cfg?.claude_has_key ? 'text-emerald-400' : 'text-slate-500'}`}>
                    {cfg?.claude_has_key ? 'Key configured' : 'No key configured'}
                  </span>
                </div>
                <p className="text-[11px] text-slate-500 mt-1">
                  From{' '}
                  <a href="https://console.anthropic.com" target="_blank" rel="noopener noreferrer" className="text-sky-400 hover:underline">console.anthropic.com</a>.
                  Stored encrypted.
                </p>
              </div>
              <div>
                <label className="ng-label" htmlFor="ai-anthropic-model">Model</label>
                <input
                  id="ai-anthropic-model"
                  value={anthropicModel}
                  onChange={(e) => setAnthropicModel(e.target.value)}
                  className={inputCls}
                  placeholder={cfg?.ai_anthropic_default_model}
                />
              </div>
            </>
          ) : (
            <>
              <div>
                <label className="ng-label" htmlFor="ai-base-url">Base URL</label>
                <input
                  id="ai-base-url"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  className={inputCls}
                  placeholder="http://10.0.0.5:11434/v1"
                />
                <p className="text-[11px] text-slate-500 mt-1">
                  Ollama <code>http://host:11434/v1</code> · LM Studio <code>http://host:1234/v1</code> · vLLM <code>http://host:8000/v1</code> ·
                  Azure <code>https://&lt;resource&gt;.openai.azure.com</code>. Local and private addresses are allowed;
                  <code> localhost</code> is the Nodeglow container itself.
                </p>
              </div>
              <div>
                <label className="ng-label" htmlFor="ai-openai-model">Model {apiVersion.trim() ? '(Azure deployment name)' : ''}</label>
                <input
                  id="ai-openai-model"
                  value={openaiModel}
                  onChange={(e) => setOpenaiModel(e.target.value)}
                  className={inputCls}
                  placeholder={apiVersion.trim() ? 'gpt-4o-mini-chn' : 'llama3.1:8b'}
                />
              </div>
              <div>
                <label className="ng-label" htmlFor="ai-api-version">Azure API version (Azure OpenAI only)</label>
                <input
                  id="ai-api-version"
                  value={apiVersion}
                  onChange={(e) => setApiVersion(e.target.value)}
                  className={inputCls}
                  placeholder="2024-10-21 (leave empty for non-Azure)"
                />
              </div>
              <div>
                <label className="ng-label" htmlFor="ai-openai-key">API key {apiVersion.trim() ? '' : '(optional for local servers)'}</label>
                <div className="flex items-center gap-2">
                  <input
                    id="ai-openai-key"
                    type="password"
                    value={openaiKey}
                    onChange={(e) => { setOpenaiKey(e.target.value); setClearOpenaiKey(false); }}
                    className={inputCls}
                    placeholder={cfg?.ai_openai_has_key ? '•••••••• (keep current key)' : ''}
                    autoComplete="off"
                    disabled={clearOpenaiKey}
                  />
                  {cfg?.ai_openai_has_key && (
                    <label className="flex items-center gap-1.5 text-xs text-slate-400 whitespace-nowrap cursor-pointer">
                      <input type="checkbox" checked={clearOpenaiKey} onChange={(e) => setClearOpenaiKey(e.target.checked)} className={checkboxCls} />
                      Remove key
                    </label>
                  )}
                </div>
              </div>
            </>
          )}

          <div className="flex flex-wrap items-center gap-3 pt-1">
            <Button size="sm" variant="secondary" onClick={testConnection} disabled={testing || keyMissing} data-testid="ai-test-connection">
              <Plug size={12} />
              {testing ? 'Testing…' : 'Test connection'}
            </Button>
            <span className="text-[11px] text-slate-500">Sends a fixed “Reply with OK” prompt, no infrastructure data.</span>
          </div>
          {testResult && (
            <p
              role="status"
              className={`text-xs rounded-md px-3 py-2 border ${testResult.ok
                ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20'
                : 'text-red-300 bg-red-500/10 border-red-500/20'}`}
            >
              {testResult.message}
            </p>
          )}
        </div>
      </GlassCard>

      {/* Redaction */}
      <GlassCard className="p-4">
        <h3 className="text-base font-semibold text-slate-200 mb-1 flex items-center gap-2">
          <ShieldCheck size={16} className="text-violet-400" />
          Redaction
        </h3>
        <p className="text-xs text-slate-400 mb-4">
          Personal data is replaced by stable placeholders before anything leaves Nodeglow; the same value always gets the same placeholder within one request, so the model can still correlate.
        </p>
        <div className="space-y-2">
          <label className="flex items-start gap-2 cursor-pointer">
            <input type="checkbox" checked={redact} onChange={(e) => setRedact(e.target.checked)} className={`${checkboxCls} mt-0.5`} />
            <span className="text-sm text-[var(--ng-text-primary)]">
              Redact personal data and secrets
              <span className="block text-[11px] text-slate-500">IPv4/IPv6, MAC, e-mail, usernames (sshd, PAM, sudo, Windows events, nginx), Windows SIDs and workstation names, passwords/tokens/API keys.</span>
            </span>
          </label>
          <label className="flex items-start gap-2 cursor-pointer">
            <input type="checkbox" checked={redactHosts} disabled={!redact} onChange={(e) => setRedactHosts(e.target.checked)} className={`${checkboxCls} mt-0.5`} />
            <span className="text-sm text-[var(--ng-text-primary)]">
              Also redact hostnames and FQDNs
              <span className="block text-[11px] text-slate-500">Includes the names of your monitored hosts and integrations. Answers become less specific.</span>
            </span>
          </label>
          <label className="flex items-start gap-2 cursor-pointer">
            <input type="checkbox" checked={restore} disabled={!redact} onChange={(e) => setRestore(e.target.checked)} className={`${checkboxCls} mt-0.5`} />
            <span className="text-sm text-[var(--ng-text-primary)]">
              Show real values in answers
              <span className="block text-[11px] text-slate-500">Placeholders are mapped back inside Nodeglow only; the mapping is never sent. Secrets always stay masked.</span>
            </span>
          </label>
        </div>
      </GlassCard>

      {/* Daily AI Summary */}
      <GlassCard className="p-4">
        <h3 className="text-base font-semibold text-slate-200 mb-1 flex items-center gap-2">
          <Bell size={16} className="text-violet-400" />
          Daily AI Summary
        </h3>
        <p className="text-xs text-slate-400 mb-4">
          Sends a daily AI-generated briefing with incidents, root cause analysis, and resolution suggestions via your selected notification channels.
        </p>
        <div className="space-y-3">
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={dailyEnabled} onChange={(e) => setDailyEnabled(e.target.checked)} className={checkboxCls} />
            <span className="text-sm text-[var(--ng-text-primary)]">Enable daily AI summary</span>
          </label>
          <div>
            <label className="ng-label" htmlFor="ai-daily-hour">Send at (UTC)</label>
            <select id="ai-daily-hour" value={dailyHour} onChange={(e) => setDailyHour(e.target.value)} className={selectSmCls} disabled={!dailyEnabled}>
              {Array.from({ length: 24 }, (_, i) => (
                <option key={i} value={String(i)}>{String(i).padStart(2, '0')}:00</option>
              ))}
            </select>
          </div>
          <div>
            <p className="ng-label">Channels</p>
            <div className="flex flex-wrap gap-3 mt-1">
              {ALL_CHANNELS.map((ch) => (
                <label key={ch} className="flex items-center gap-1.5 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={dailyChannels.has(ch)}
                    disabled={!dailyEnabled}
                    onChange={(e) => {
                      const next = new Set(dailyChannels);
                      if (e.target.checked) next.add(ch); else next.delete(ch);
                      setDailyChannels(next);
                    }}
                    className={checkboxCls}
                  />
                  <span className="text-xs text-[var(--ng-text-secondary)] capitalize">{ch}</span>
                </label>
              ))}
            </div>
          </div>
          {!cfg?.ai_enabled && (
            <p className="text-xs text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded-md px-3 py-2">
              Runs only while AI features are enabled (above).
            </p>
          )}
        </div>
      </GlassCard>

      <AiUsageCard />

      <div className="flex justify-end gap-2">
        <Button
          size="sm"
          variant="ghost"
          disabled={testingSummary || !cfg?.ai_enabled}
          title={!cfg?.ai_enabled ? 'Enable and save AI features first' : undefined}
          onClick={async () => {
            setTestingSummary(true);
            try {
              if (!(await save())) return;
              const res = await post<{ ok: boolean; message: string }>('/settings/ai/test-summary');
              toast.show(res.message || 'Test summary sent', 'success');
            } catch (e) {
              const body = apiErrorBody(e);
              toast.show((typeof body?.message === 'string' && body.message) || 'Test summary failed — check server logs', 'error');
            } finally {
              setTestingSummary(false);
            }
          }}
        >
          <Send size={12} />
          {testingSummary ? 'Generating...' : 'Test Summary'}
        </Button>
        <Button size="sm" disabled={saving} onClick={() => { void save(); }} data-testid="ai-save">
          {saving ? 'Saving...' : 'Save AI Settings'}
        </Button>
      </div>
    </div>
  );
}
