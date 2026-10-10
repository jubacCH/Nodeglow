/**
 * Settings page: server payload, per-section form values and the request
 * bodies built from them. Pure functions so the wire format can be unit
 * tested — the backend contract (`/settings/save`, `/settings/notifications/save`,
 * `/settings/digest/save`, `/settings/ldap/save`) must stay byte-compatible.
 */

export interface SettingsData {
  site_name: string;
  agent_server_url: string;
  timezone: string;
  ping_interval: string;
  latency_threshold_ms: string;
  proxmox_interval: string;
  ping_retention_days: string;
  proxmox_retention_days: string;
  integration_retention_days: string;
  incident_event_retention_days: string;
  anomaly_threshold: string;
  proxmox_cpu_threshold: string;
  proxmox_ram_threshold: string;
  proxmox_disk_threshold: string;
  syslog_port: string;
  syslog_allowlist_only: string;
  digest_enabled: string;
  digest_day: string;
  digest_hour: string;
  notify_enabled: string;
  notify_grace_minutes: string;
  correlation_min_failures: string;
  correlation_min_cycles: string;
  predictor_min_confidence: string;
  predictor_min_occurrences: string;
  predictor_template_blacklist: string; // JSON-stringified array of regex strings
  telegram_chat_id: string;
  // Channel secrets are write-only: the backend only says whether one is set.
  telegram_bot_token_has_value?: boolean;
  discord_webhook_url_has_value?: boolean;
  webhook_url_has_value?: boolean;
  webhook_secret_has_value?: boolean;
  smtp_host: string;
  smtp_port: string;
  smtp_user: string;
  smtp_from: string;
  smtp_to: string;
  smtp_has_pw: boolean;
  claude_has_key: boolean;
  daily_ai_summary_enabled: string;
  daily_ai_summary_hour: string;
  daily_ai_summary_channels: string;
  notify_telegram_min_severity: string;
  notify_discord_min_severity: string;
  notify_webhook_min_severity: string;
  notify_email_min_severity: string;
  // Teams / Slack / ntfy (webhook URLs + token are write-only: flags only)
  public_url?: string;
  teams_enabled?: string;
  teams_has_url?: boolean;
  notify_teams_min_severity?: string;
  slack_enabled?: string;
  slack_has_url?: boolean;
  notify_slack_min_severity?: string;
  ntfy_enabled?: string;
  ntfy_server_url?: string;
  ntfy_topic?: string;
  ntfy_has_token?: boolean;
  notify_ntfy_min_severity?: string;
  // LDAP
  ldap_enabled: string;
  ldap_server: string;
  ldap_bind_dn: string;
  ldap_has_bind_pw: boolean;
  ldap_base_dn: string;
  ldap_user_filter: string;
  ldap_display_attr: string;
  ldap_group_attr: string;
  ldap_admin_group: string;
  ldap_editor_group: string;
  ldap_use_ssl: string;
  ldap_start_tls: string;
  /** "1" (default) or "0"; absent on backends that predate the setting. */
  ldap_tls_verify?: string;
}

/* ---------- Template blacklist (textarea <-> JSON) ---------- */

export function blacklistAsLines(jsonStr: string): string {
  try {
    const arr = JSON.parse(jsonStr);
    return Array.isArray(arr) ? arr.join('\n') : '';
  } catch {
    return '';
  }
}

export function linesToBlacklistJson(text: string): string {
  const lines = text
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l.length > 0);
  return JSON.stringify(lines);
}

/* ---------- General (System + Monitoring tabs share one endpoint) ---------- */

export interface GeneralForm {
  siteName: string;
  agentServerUrl: string;
  timezone: string;
  pingInterval: string;
  latencyThreshold: string;
  proxmoxInterval: string;
  pingRetention: string;
  proxmoxRetention: string;
  integrationRetention: string;
  incidentEventRetention: string;
  anomalyThreshold: string;
  cpuThreshold: string;
  ramThreshold: string;
  diskThreshold: string;
  syslogPort: string;
  syslogAllowlist: boolean;
  predictorMinConfidence: string;
  predictorMinOccurrences: string;
  /** Raw textarea text, one regex per line (converted to JSON on save). */
  predictorBlacklistText: string;
}

export function generalFromSettings(s: SettingsData): GeneralForm {
  return {
    siteName: s.site_name,
    agentServerUrl: s.agent_server_url || '',
    timezone: s.timezone,
    pingInterval: s.ping_interval,
    latencyThreshold: s.latency_threshold_ms,
    proxmoxInterval: s.proxmox_interval,
    pingRetention: s.ping_retention_days || '30',
    proxmoxRetention: s.proxmox_retention_days || '7',
    integrationRetention: s.integration_retention_days || '7',
    incidentEventRetention: s.incident_event_retention_days || '30',
    anomalyThreshold: s.anomaly_threshold || '2.0',
    cpuThreshold: s.proxmox_cpu_threshold || '85',
    ramThreshold: s.proxmox_ram_threshold || '85',
    diskThreshold: s.proxmox_disk_threshold || '90',
    syslogPort: s.syslog_port || '1514',
    syslogAllowlist: s.syslog_allowlist_only === '1',
    predictorMinConfidence: s.predictor_min_confidence || '0.8',
    predictorMinOccurrences: s.predictor_min_occurrences || '3',
    predictorBlacklistText: blacklistAsLines(s.predictor_template_blacklist || '[]'),
  };
}

export function buildGeneralParams(f: GeneralForm): URLSearchParams {
  const params = new URLSearchParams();
  params.set('site_name', f.siteName);
  params.set('agent_server_url', f.agentServerUrl);
  params.set('timezone', f.timezone);
  params.set('ping_interval', f.pingInterval);
  params.set('latency_threshold', f.latencyThreshold);
  params.set('proxmox_interval', f.proxmoxInterval);
  params.set('ping_retention', f.pingRetention);
  params.set('proxmox_retention', f.proxmoxRetention);
  params.set('integration_retention', f.integrationRetention);
  params.set('incident_event_retention', f.incidentEventRetention);
  params.set('anomaly_threshold', f.anomalyThreshold);
  params.set('cpu_threshold', f.cpuThreshold);
  params.set('ram_threshold', f.ramThreshold);
  params.set('disk_threshold', f.diskThreshold);
  params.set('syslog_port', f.syslogPort);
  params.set('syslog_allowlist_only', f.syslogAllowlist ? '1' : '0');
  params.set('predictor_min_confidence', f.predictorMinConfidence);
  params.set('predictor_min_occurrences', f.predictorMinOccurrences);
  params.set('predictor_template_blacklist', linesToBlacklistJson(f.predictorBlacklistText));
  return params;
}

/* ---------- Notifications ---------- */

export type LegacySecretKey = 'telegram_bot_token' | 'discord_webhook_url' | 'webhook_url' | 'webhook_secret';

export interface NotifForm {
  notifyEnabled: boolean;
  graceMinutes: string;
  corrMinFailures: string;
  corrMinCycles: string;
  telegramToken: string;
  telegramChat: string;
  discordWebhook: string;
  webhookUrl: string;
  webhookSecret: string;
  /** Legacy secrets marked for removal on the next save (sorted). */
  clearSecrets: LegacySecretKey[];
  smtpHost: string;
  smtpPort: string;
  smtpUser: string;
  smtpPassword: string;
  smtpFrom: string;
  smtpTo: string;
  telegramMinSev: string;
  discordMinSev: string;
  webhookMinSev: string;
  emailMinSev: string;
  publicUrl: string;
  teamsEnabled: boolean;
  teamsUrl: string;
  teamsUrlClear: boolean;
  teamsMinSev: string;
  slackEnabled: boolean;
  slackUrl: string;
  slackUrlClear: boolean;
  slackMinSev: string;
  ntfyEnabled: boolean;
  ntfyServer: string;
  ntfyTopic: string;
  ntfyToken: string;
  ntfyTokenClear: boolean;
  ntfyMinSev: string;
}

export function notifFromSettings(s: SettingsData): NotifForm {
  return {
    notifyEnabled: s.notify_enabled === '1',
    graceMinutes: s.notify_grace_minutes || '5',
    corrMinFailures: s.correlation_min_failures || '3',
    corrMinCycles: s.correlation_min_cycles || '2',
    telegramToken: '',
    telegramChat: s.telegram_chat_id,
    discordWebhook: '',
    webhookUrl: '',
    webhookSecret: '',
    clearSecrets: [],
    smtpHost: s.smtp_host,
    smtpPort: s.smtp_port,
    smtpUser: s.smtp_user,
    smtpPassword: '',
    smtpFrom: s.smtp_from,
    smtpTo: s.smtp_to,
    telegramMinSev: s.notify_telegram_min_severity || 'all',
    discordMinSev: s.notify_discord_min_severity || 'all',
    webhookMinSev: s.notify_webhook_min_severity || 'all',
    emailMinSev: s.notify_email_min_severity || 'all',
    publicUrl: s.public_url || '',
    teamsEnabled: s.teams_enabled === '1',
    teamsUrl: '',
    teamsUrlClear: false,
    teamsMinSev: s.notify_teams_min_severity || 'all',
    slackEnabled: s.slack_enabled === '1',
    slackUrl: '',
    slackUrlClear: false,
    slackMinSev: s.notify_slack_min_severity || 'all',
    ntfyEnabled: s.ntfy_enabled === '1',
    ntfyServer: s.ntfy_server_url || 'https://ntfy.sh',
    ntfyTopic: s.ntfy_topic || '',
    ntfyToken: '',
    ntfyTokenClear: false,
    ntfyMinSev: s.notify_ntfy_min_severity || 'all',
  };
}

export function buildNotificationParams(f: NotifForm): URLSearchParams {
  const params = new URLSearchParams();
  params.set('notify_enabled', f.notifyEnabled ? 'on' : '0');
  params.set('notify_grace_minutes', f.graceMinutes);
  params.set('correlation_min_failures', f.corrMinFailures);
  params.set('correlation_min_cycles', f.corrMinCycles);
  // Legacy channel secrets: blank keeps the stored value, *_clear removes it.
  params.set('telegram_bot_token', f.telegramToken);
  params.set('telegram_chat_id', f.telegramChat);
  params.set('discord_webhook_url', f.discordWebhook);
  params.set('webhook_url', f.webhookUrl);
  params.set('webhook_secret', f.webhookSecret);
  for (const key of f.clearSecrets) params.set(`${key}_clear`, '1');
  params.set('smtp_host', f.smtpHost);
  params.set('smtp_port', f.smtpPort);
  params.set('smtp_user', f.smtpUser);
  params.set('smtp_password', f.smtpPassword);
  params.set('smtp_from', f.smtpFrom);
  params.set('smtp_to', f.smtpTo);
  params.set('notify_telegram_min_severity', f.telegramMinSev);
  params.set('notify_discord_min_severity', f.discordMinSev);
  params.set('notify_webhook_min_severity', f.webhookMinSev);
  params.set('notify_email_min_severity', f.emailMinSev);
  params.set('public_url', f.publicUrl);
  // Teams / Slack / ntfy secrets: blank keeps the stored value.
  params.set('teams_enabled', f.teamsEnabled ? '1' : '0');
  params.set('teams_webhook_url', f.teamsUrlClear ? '' : f.teamsUrl);
  if (f.teamsUrlClear) params.set('teams_webhook_url_clear', '1');
  params.set('notify_teams_min_severity', f.teamsMinSev);
  params.set('slack_enabled', f.slackEnabled ? '1' : '0');
  params.set('slack_webhook_url', f.slackUrlClear ? '' : f.slackUrl);
  if (f.slackUrlClear) params.set('slack_webhook_url_clear', '1');
  params.set('notify_slack_min_severity', f.slackMinSev);
  params.set('ntfy_enabled', f.ntfyEnabled ? '1' : '0');
  params.set('ntfy_server_url', f.ntfyServer);
  params.set('ntfy_topic', f.ntfyTopic);
  params.set('ntfy_token', f.ntfyTokenClear ? '' : f.ntfyToken);
  if (f.ntfyTokenClear) params.set('ntfy_token_clear', '1');
  params.set('notify_ntfy_min_severity', f.ntfyMinSev);
  return params;
}

/* ---------- Weekly digest ---------- */

export interface DigestForm {
  digestEnabled: boolean;
  digestDay: string;
  digestHour: string;
}

export function digestFromSettings(s: SettingsData): DigestForm {
  return {
    digestEnabled: s.digest_enabled === '1',
    digestDay: s.digest_day || '0',
    digestHour: s.digest_hour || '9',
  };
}

export function buildDigestBody(f: DigestForm) {
  return { digest_enabled: f.digestEnabled, digest_day: Number(f.digestDay), digest_hour: Number(f.digestHour) };
}

/* ---------- LDAP ---------- */

export const DEFAULT_LDAP_FILTER = '(&(objectClass=person)(sAMAccountName={username}))';

export interface LdapForm {
  ldapEnabled: boolean;
  ldapServer: string;
  ldapBindDn: string;
  ldapBindPassword: string;
  ldapBaseDn: string;
  ldapUserFilter: string;
  ldapDisplayAttr: string;
  ldapGroupAttr: string;
  ldapAdminGroup: string;
  ldapEditorGroup: string;
  ldapUseSsl: boolean;
  ldapStartTls: boolean;
  ldapTlsVerify: boolean;
}

export function ldapFromSettings(s: SettingsData): LdapForm {
  return {
    ldapEnabled: s.ldap_enabled === '1',
    ldapServer: s.ldap_server || '',
    ldapBindDn: s.ldap_bind_dn || '',
    ldapBindPassword: '',
    ldapBaseDn: s.ldap_base_dn || '',
    ldapUserFilter: s.ldap_user_filter || DEFAULT_LDAP_FILTER,
    ldapDisplayAttr: s.ldap_display_attr || 'displayName',
    ldapGroupAttr: s.ldap_group_attr || 'memberOf',
    ldapAdminGroup: s.ldap_admin_group || '',
    ldapEditorGroup: s.ldap_editor_group || '',
    ldapUseSsl: s.ldap_use_ssl === '1',
    ldapStartTls: s.ldap_start_tls === '1',
    // Default on: only an explicit "0" turns certificate verification off.
    ldapTlsVerify: s.ldap_tls_verify !== '0',
  };
}

/** Body of `/settings/ldap/save`. The connection test saves first with LDAP
 *  forced on (`forceEnabled`), as before. */
export function buildLdapFormData(f: LdapForm, forceEnabled = false): FormData {
  const fd = new FormData();
  fd.append('ldap_enabled', forceEnabled || f.ldapEnabled ? '1' : '0');
  fd.append('ldap_server', f.ldapServer);
  fd.append('ldap_bind_dn', f.ldapBindDn);
  fd.append('ldap_bind_password', f.ldapBindPassword);
  fd.append('ldap_base_dn', f.ldapBaseDn);
  fd.append('ldap_user_filter', f.ldapUserFilter);
  fd.append('ldap_display_attr', f.ldapDisplayAttr);
  fd.append('ldap_group_attr', f.ldapGroupAttr);
  fd.append('ldap_admin_group', f.ldapAdminGroup);
  fd.append('ldap_editor_group', f.ldapEditorGroup);
  fd.append('ldap_use_ssl', f.ldapUseSsl ? '1' : '0');
  fd.append('ldap_start_tls', f.ldapStartTls ? '1' : '0');
  fd.append('ldap_tls_verify', f.ldapTlsVerify ? '1' : '0');
  return fd;
}

/* ---------- Dirty tracking ---------- */

export function sameValues<T extends object>(a: T, b: T): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** Normalise the blacklist text for dirty comparison (trailing newlines and
 *  blank lines do not change what is saved). */
export function generalEquals(a: GeneralForm, b: GeneralForm): boolean {
  return sameValues(
    { ...a, predictorBlacklistText: linesToBlacklistJson(a.predictorBlacklistText) },
    { ...b, predictorBlacklistText: linesToBlacklistJson(b.predictorBlacklistText) },
  );
}
