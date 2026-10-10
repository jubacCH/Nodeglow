'use client';

import type { ReactNode } from 'react';
import { Send } from 'lucide-react';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Field, Input, Select, Switch } from '@/components/ui/Field';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryState, type QueryLike } from '@/components/ui/QueryState';
import { StatusPill } from '@/components/ui/StatusPill';
import { Table, TableContainer, TBody, Td, Th, THead, Tr } from '@/components/ui/Table';
import { Code, SaveBar, SettingsSection, SetupGuide, type SaveStatus } from './formKit';
import type { DigestForm, LegacySecretKey, NotifForm, SettingsData } from './settingsForm';

export interface NotifLog {
  id: number;
  timestamp: string | null;
  channel: string;
  title: string;
  severity: string;
  status: string;
  error: string | null;
}

const SEVERITY_OPTIONS = [
  { value: 'all', label: 'All' },
  { value: 'warning', label: 'Warning+' },
  { value: 'error', label: 'Error+' },
  { value: 'critical', label: 'Critical only' },
] as const;

const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

type SetFn<T> = <K extends keyof T>(key: K, v: T[K]) => void;

/* ---------- Field helpers ---------- */

/** Write-only secret: shows whether one is stored and offers to remove it. */
function SecretField({
  label, stored, cleared, onClear, value, onChange, placeholder,
}: {
  label: string;
  stored: boolean;
  cleared?: boolean;
  /** Omit when the backend cannot remove this secret. */
  onClear?: (v: boolean) => void;
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
}) {
  return (
    <div className="min-w-0">
      <Field
        label={
          <>
            {label}
            {stored && !cleared && <span className="ml-1.5 font-normal text-ok">· stored</span>}
            {cleared && <span className="ml-1.5 font-normal text-down">· will be removed on save</span>}
          </>
        }
      >
        <Input
          type="password"
          autoComplete="off"
          value={value}
          disabled={cleared}
          onChange={(e) => onChange(e.target.value)}
          placeholder={stored ? 'Leave blank to keep' : placeholder}
        />
      </Field>
      {stored && onClear && (
        <button
          type="button"
          onClick={() => onClear(!cleared)}
          className="mt-1 rounded-chip text-meta text-fg-3 underline-offset-2 hover:text-down hover:underline"
        >
          {cleared ? 'Undo remove' : 'Remove stored value'}
        </button>
      )}
    </div>
  );
}

function SeverityField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <Field label="Minimum severity">
      <Select className="max-w-[200px]" value={value} onChange={(e) => onChange(e.target.value)}>
        {SEVERITY_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </Select>
    </Field>
  );
}

function ChannelCard({
  id, title, enabled, onToggle, configured, testing, onTest, guide, children,
}: {
  id: string;
  title: string;
  /** Only channels with their own on/off flag (Teams, Slack, ntfy). */
  enabled?: boolean;
  onToggle?: (v: boolean) => void;
  configured: boolean;
  testing: boolean;
  onTest: () => void;
  guide?: ReactNode[];
  children: ReactNode;
}) {
  return (
    <SettingsSection
      id={`ch-${id}`}
      title={
        <span className="inline-flex items-center gap-2">
          {title}
          {configured ? <Badge tone="accent">Configured</Badge> : <Badge>Not set up</Badge>}
        </span>
      }
      actions={
        <>
          {onToggle && <Switch checked={!!enabled} onChange={onToggle} aria-label={`Enable ${title}`} />}
          <Button size="sm" variant="secondary" onClick={onTest} loading={testing} disabled={testing}>
            {!testing && <Send size={13} aria-hidden="true" />}
            {testing ? 'Sending…' : 'Send test'}
          </Button>
        </>
      }
    >
      {guide && <SetupGuide steps={guide} />}
      {children}
    </SettingsSection>
  );
}

/* ---------- Tab ---------- */

export function NotificationsTab({
  settings, form, set, status, onSave, onDiscard, testingChannel, onTest,
  digest, setDigest, digestStatus, onSaveDigest, onDiscardDigest, history,
}: {
  settings: SettingsData;
  form: NotifForm;
  set: SetFn<NotifForm>;
  status: SaveStatus;
  onSave: () => void;
  onDiscard: () => void;
  testingChannel: string | null;
  onTest: (channel: string) => void;
  digest: DigestForm;
  setDigest: SetFn<DigestForm>;
  digestStatus: SaveStatus;
  onSaveDigest: () => void;
  onDiscardDigest: () => void;
  history: QueryLike<NotifLog[]>;
}) {
  const legacy = (key: LegacySecretKey) => ({
    stored: !!settings[`${key}_has_value`],
    cleared: form.clearSecrets.includes(key),
    onClear: (v: boolean) => {
      const next = new Set(form.clearSecrets);
      if (v) next.add(key); else next.delete(key);
      set('clearSecrets', Array.from(next).sort());
    },
  });
  const testProps = (ch: string) => ({ testing: testingChannel === ch, onTest: () => onTest(ch) });

  return (
    <div className="space-y-4">
      {/* Channels + delivery rules share one save; the bar sticks while scrolling through them. */}
      <div className="space-y-4">
        <SettingsSection id="delivery" title="Delivery" description="When Nodeglow sends alerts for incidents.">
          <div className="space-y-5">
            <Switch
              checked={form.notifyEnabled}
              onChange={(v) => set('notifyEnabled', v)}
              label="Send notifications"
              description="Send alerts when incidents are created or resolved."
            />
            <div className="grid gap-4 sm:grid-cols-3">
              <Field label="Grace period (min)" hint="Wait before sending an offline alert; skips brief outages.">
                <Input type="number" className="num max-w-[160px]" min={0} max={60} value={form.graceMinutes} onChange={(e) => set('graceMinutes', e.target.value)} />
              </Field>
              <Field label="Min. consecutive ping failures" hint="Pings in a row before a host counts as offline.">
                <Input type="number" className="num max-w-[160px]" min={1} max={10} value={form.corrMinFailures} onChange={(e) => set('corrMinFailures', e.target.value)} />
              </Field>
              <Field label="Min. correlation cycles" hint="60 s cycles a condition must match before an incident is created.">
                <Input type="number" className="num max-w-[160px]" min={1} max={10} value={form.corrMinCycles} onChange={(e) => set('corrMinCycles', e.target.value)} />
              </Field>
            </div>
          </div>
        </SettingsSection>

        <ChannelCard
          id="telegram"
          title="Telegram"
          configured={!!settings.telegram_bot_token_has_value && !!form.telegramChat}
          {...testProps('telegram')}
          guide={[
            <>Open Telegram and search for <strong>@BotFather</strong></>,
            <>Send <Code>/newbot</Code> and follow the prompts to name your bot</>,
            <>BotFather replies with a <strong>bot token</strong> like <Code>123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11</Code> — paste it below</>,
            <>Add the bot to your group or channel, then send a message in the chat</>,
            <>Get your <strong>chat ID</strong>: open <Code>https://api.telegram.org/bot&lt;TOKEN&gt;/getUpdates</Code> in your browser and look for <Code>&quot;chat&quot;:{'{'}&quot;id&quot;:-100…</Code></>,
          ]}
        >
          <div className="grid gap-4 sm:grid-cols-3">
            <SecretField label="Bot token" {...legacy('telegram_bot_token')} value={form.telegramToken} onChange={(v) => set('telegramToken', v)} placeholder="123456:ABC-DEF..." />
            <Field label="Chat ID">
              <Input className="font-mono" value={form.telegramChat} onChange={(e) => set('telegramChat', e.target.value)} placeholder="-1001234567890" />
            </Field>
            <SeverityField value={form.telegramMinSev} onChange={(v) => set('telegramMinSev', v)} />
          </div>
        </ChannelCard>

        <ChannelCard
          id="discord"
          title="Discord"
          configured={!!settings.discord_webhook_url_has_value}
          {...testProps('discord')}
          guide={[
            <>Open your Discord server and go to <strong>Server Settings</strong> &gt; <strong>Integrations</strong></>,
            <>Click <strong>Webhooks</strong> &gt; <strong>New Webhook</strong></>,
            <>Choose a name (e.g. &quot;Nodeglow&quot;) and select the channel for alerts</>,
            <>Click <strong>Copy Webhook URL</strong> — it looks like <Code>https://discord.com/api/webhooks/123.../abc...</Code></>,
            <>Paste the URL below and click <strong>Send test</strong> to verify</>,
          ]}
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <SecretField label="Webhook URL" {...legacy('discord_webhook_url')} value={form.discordWebhook} onChange={(v) => set('discordWebhook', v)} placeholder="https://discord.com/api/webhooks/..." />
            <SeverityField value={form.discordMinSev} onChange={(v) => set('discordMinSev', v)} />
          </div>
        </ChannelCard>

        <ChannelCard id="webhook" title="Webhook" configured={!!settings.webhook_url_has_value} {...testProps('webhook')}>
          <div className="grid gap-4 sm:grid-cols-3">
            <SecretField label="URL" {...legacy('webhook_url')} value={form.webhookUrl} onChange={(v) => set('webhookUrl', v)} placeholder="https://example.com/webhook" />
            <SecretField label="Secret" {...legacy('webhook_secret')} value={form.webhookSecret} onChange={(v) => set('webhookSecret', v)} placeholder="Optional signing secret" />
            <SeverityField value={form.webhookMinSev} onChange={(v) => set('webhookMinSev', v)} />
          </div>
        </ChannelCard>

        <ChannelCard id="email" title="Email / SMTP" configured={!!form.smtpHost && !!form.smtpTo} {...testProps('email')}>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Field label="SMTP host">
              <Input value={form.smtpHost} onChange={(e) => set('smtpHost', e.target.value)} placeholder="smtp.example.com" />
            </Field>
            <Field label="Port">
              <Input type="number" className="num max-w-[160px]" value={form.smtpPort} onChange={(e) => set('smtpPort', e.target.value)} placeholder="587" />
            </Field>
            <Field label="Username">
              <Input autoComplete="off" value={form.smtpUser} onChange={(e) => set('smtpUser', e.target.value)} placeholder="user@example.com" />
            </Field>
            <SecretField label="Password" stored={!!settings.smtp_has_pw} value={form.smtpPassword} onChange={(v) => set('smtpPassword', v)} placeholder="Password" />
            <Field label="From address">
              <Input value={form.smtpFrom} onChange={(e) => set('smtpFrom', e.target.value)} placeholder="nodeglow@example.com" />
            </Field>
            <Field label="To address">
              <Input value={form.smtpTo} onChange={(e) => set('smtpTo', e.target.value)} placeholder="admin@example.com" />
            </Field>
            <SeverityField value={form.emailMinSev} onChange={(v) => set('emailMinSev', v)} />
          </div>
        </ChannelCard>

        <ChannelCard
          id="teams"
          title="Microsoft Teams"
          enabled={form.teamsEnabled}
          onToggle={(v) => set('teamsEnabled', v)}
          configured={!!settings.teams_has_url}
          {...testProps('teams')}
          guide={[
            <>In the Teams channel, open <strong>Workflows</strong> (… &gt; <strong>Workflows</strong>)</>,
            <>Choose the template <strong>Post to a channel when a webhook request is received</strong></>,
            <>Pick team and channel, then finish — Teams shows the <strong>webhook URL</strong></>,
            <>Paste the URL below and click <strong>Send test</strong>. Alerts arrive as Adaptive Cards.</>,
          ]}
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <SecretField
              label="Workflow webhook URL"
              stored={!!settings.teams_has_url}
              cleared={form.teamsUrlClear}
              onClear={(v) => set('teamsUrlClear', v)}
              value={form.teamsUrl}
              onChange={(v) => set('teamsUrl', v)}
              placeholder="https://…logic.azure.com/workflows/…"
            />
            <SeverityField value={form.teamsMinSev} onChange={(v) => set('teamsMinSev', v)} />
          </div>
        </ChannelCard>

        <ChannelCard
          id="slack"
          title="Slack"
          enabled={form.slackEnabled}
          onToggle={(v) => set('slackEnabled', v)}
          configured={!!settings.slack_has_url}
          {...testProps('slack')}
          guide={[
            <>Create a Slack app at <Code>api.slack.com/apps</Code> (or open an existing one)</>,
            <>Enable <strong>Incoming Webhooks</strong> and click <strong>Add New Webhook to Workspace</strong></>,
            <>Pick the channel — the URL looks like <Code>https://hooks.slack.com/services/T…/B…/…</Code></>,
            <>Paste it below and click <strong>Send test</strong></>,
          ]}
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <SecretField
              label="Webhook URL"
              stored={!!settings.slack_has_url}
              cleared={form.slackUrlClear}
              onClear={(v) => set('slackUrlClear', v)}
              value={form.slackUrl}
              onChange={(v) => set('slackUrl', v)}
              placeholder="https://hooks.slack.com/services/..."
            />
            <SeverityField value={form.slackMinSev} onChange={(v) => set('slackMinSev', v)} />
          </div>
        </ChannelCard>

        <ChannelCard
          id="ntfy"
          title="ntfy"
          enabled={form.ntfyEnabled}
          onToggle={(v) => set('ntfyEnabled', v)}
          configured={!!form.ntfyTopic}
          {...testProps('ntfy')}
          guide={[
            <>Use <Code>https://ntfy.sh</Code> or your own ntfy server</>,
            <>Pick a topic name — on the public server it acts like a password, so make it hard to guess</>,
            <>Subscribe to the topic in the ntfy app; for protected topics create an <strong>access token</strong> (<Code>tk_…</Code>) or use <Code>user:password</Code></>,
            <>Severity sets the push priority (critical = urgent)</>,
          ]}
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Server URL">
              <Input inputMode="url" value={form.ntfyServer} onChange={(e) => set('ntfyServer', e.target.value)} placeholder="https://ntfy.sh" />
            </Field>
            <Field label="Topic">
              <Input className="font-mono" value={form.ntfyTopic} onChange={(e) => set('ntfyTopic', e.target.value)} placeholder="nodeglow-alerts-x7k2" />
            </Field>
            <SecretField
              label="Access token"
              stored={!!settings.ntfy_has_token}
              cleared={form.ntfyTokenClear}
              onClear={(v) => set('ntfyTokenClear', v)}
              value={form.ntfyToken}
              onChange={(v) => set('ntfyToken', v)}
              placeholder="Optional"
            />
            <SeverityField value={form.ntfyMinSev} onChange={(v) => set('ntfyMinSev', v)} />
          </div>
        </ChannelCard>

        <SettingsSection
          id="public-url"
          title="Public URL"
          description="Address of this Nodeglow as users open it. Teams, Slack and ntfy alerts link to the incident when set."
        >
          <Field label="Public URL" className="max-w-xl">
            <Input inputMode="url" value={form.publicUrl} onChange={(e) => set('publicUrl', e.target.value)} placeholder="https://nodeglow.example.com" />
          </Field>
        </SettingsSection>

        <SaveBar
          status={status}
          onSave={onSave}
          onDiscard={onDiscard}
          label="Save notification settings"
          note="“Send test” saves these settings first."
        />
      </div>

      <SettingsSection
        id="digest"
        title="Weekly digest email"
        description="A weekly summary of incidents, host uptime, syslog stats and certificate expiry. Requires SMTP above."
      >
        <div className="space-y-4">
          <Switch checked={digest.digestEnabled} onChange={(v) => setDigest('digestEnabled', v)} label="Send weekly digest" />
          <div className="grid max-w-md grid-cols-2 gap-4">
            <Field label="Day of week">
              <Select value={digest.digestDay} disabled={!digest.digestEnabled} onChange={(e) => setDigest('digestDay', e.target.value)}>
                {DAYS.map((d, i) => <option key={d} value={String(i)}>{d}</option>)}
              </Select>
            </Field>
            <Field label="Hour (UTC)">
              <Select value={digest.digestHour} disabled={!digest.digestEnabled} onChange={(e) => setDigest('digestHour', e.target.value)}>
                {Array.from({ length: 24 }, (_, i) => (
                  <option key={i} value={String(i)}>{String(i).padStart(2, '0')}:00</option>
                ))}
              </Select>
            </Field>
          </div>
        </div>
        <SaveBar status={digestStatus} onSave={onSaveDigest} onDiscard={onDiscardDigest} label="Save digest settings" sticky={false} />
      </SettingsSection>

      <SettingsSection id="history" title="Notification history" description="Last 20 deliveries. Refreshes every 30 seconds.">
        <QueryState
          query={history}
          compact
          empty={<EmptyState compact title="No notifications sent yet" description="Deliveries and test messages appear here." />}
        >
          {(rows) => (
            <TableContainer className="relative" maxHeight={360}>
              <Table density="compact">
                <THead sticky>
                  <Tr>
                    <Th>Status</Th>
                    <Th>Time</Th>
                    <Th>Channel</Th>
                    <Th>Title</Th>
                    <Th>Severity</Th>
                  </Tr>
                </THead>
                <TBody>
                  {rows.slice(0, 20).map((n) => (
                    <Tr key={n.id}>
                      <Td>
                        {n.status === 'sent' ? (
                          <StatusPill status="ok" size="sm">Sent</StatusPill>
                        ) : (
                          <span className="inline-flex items-center gap-2">
                            <StatusPill status="down" size="sm">Failed</StatusPill>
                            {n.error && <span className="max-w-[220px] truncate text-meta text-fg-2" title={n.error}>{n.error}</span>}
                          </span>
                        )}
                      </Td>
                      <Td muted className="num whitespace-nowrap text-meta">
                        {n.timestamp ? new Date(n.timestamp).toLocaleString() : '—'}
                      </Td>
                      <Td><Badge>{n.channel}</Badge></Td>
                      <Td className="max-w-[280px] truncate" title={n.title}>{n.title}</Td>
                      <Td>
                        <Badge variant="severity" severity={n.severity === 'critical' ? 'critical' : n.severity === 'warning' ? 'warning' : 'info'}>
                          {n.severity}
                        </Badge>
                      </Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            </TableContainer>
          )}
        </QueryState>
      </SettingsSection>
    </div>
  );
}
