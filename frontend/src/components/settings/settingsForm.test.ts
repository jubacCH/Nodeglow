import { describe, expect, it } from 'vitest';
import {
  blacklistAsLines, buildDigestBody, buildGeneralParams, buildLdapFormData, buildNotificationParams,
  digestFromSettings, generalEquals, generalFromSettings, ldapFromSettings, linesToBlacklistJson,
  notifFromSettings, type SettingsData,
} from './settingsForm';

const base = {
  site_name: 'Nodeglow', agent_server_url: '', timezone: 'Europe/Zurich',
  ping_interval: '60', latency_threshold_ms: '5000', proxmox_interval: '60',
  ping_retention_days: '', proxmox_retention_days: '14', integration_retention_days: '',
  incident_event_retention_days: '0', anomaly_threshold: '', proxmox_cpu_threshold: '',
  proxmox_ram_threshold: '', proxmox_disk_threshold: '', syslog_port: '', syslog_allowlist_only: '1',
  digest_enabled: '1', digest_day: '4', digest_hour: '',
  notify_enabled: '1', notify_grace_minutes: '', correlation_min_failures: '4', correlation_min_cycles: '',
  predictor_min_confidence: '', predictor_min_occurrences: '', predictor_template_blacklist: '["^dhcp", "ntp sync"]',
  telegram_chat_id: '-100', telegram_bot_token_has_value: true,
  smtp_host: 'smtp.example.com', smtp_port: '587', smtp_user: 'u', smtp_from: 'a@example.com', smtp_to: 'b@example.com',
  smtp_has_pw: true, claude_has_key: false,
  daily_ai_summary_enabled: '0', daily_ai_summary_hour: '8', daily_ai_summary_channels: '',
  notify_telegram_min_severity: 'critical', notify_discord_min_severity: '', notify_webhook_min_severity: '',
  notify_email_min_severity: 'warning',
  teams_enabled: '1', slack_enabled: '0', ntfy_enabled: '0', ntfy_server_url: '', ntfy_topic: 't',
  ldap_enabled: '0', ldap_server: 'ldaps://dc.example.com', ldap_bind_dn: '', ldap_has_bind_pw: false,
  ldap_base_dn: '', ldap_user_filter: '', ldap_display_attr: '', ldap_group_attr: '', ldap_admin_group: '',
  ldap_editor_group: '', ldap_use_ssl: '1', ldap_start_tls: '0',
} satisfies SettingsData;

describe('general settings', () => {
  it('fills defaults and sends the legacy field names', () => {
    const f = generalFromSettings(base);
    const p = buildGeneralParams(f);
    expect(p.get('site_name')).toBe('Nodeglow');
    expect(p.get('latency_threshold')).toBe('5000');
    expect(p.get('ping_retention')).toBe('30');
    expect(p.get('proxmox_retention')).toBe('14');
    expect(p.get('syslog_port')).toBe('1514');
    expect(p.get('syslog_allowlist_only')).toBe('1');
    expect(p.get('predictor_min_confidence')).toBe('0.8');
    expect(p.get('predictor_template_blacklist')).toBe('["^dhcp","ntp sync"]');
  });

  it('keeps blank lines out of the blacklist but lets the textarea hold them', () => {
    expect(blacklistAsLines('["a","b"]')).toBe('a\nb');
    expect(blacklistAsLines('not json')).toBe('');
    expect(linesToBlacklistJson('a\n\n  b  \n')).toBe('["a","b"]');
    const f = generalFromSettings(base);
    expect(generalEquals(f, { ...f, predictorBlacklistText: `${f.predictorBlacklistText}\n` })).toBe(true);
    expect(generalEquals(f, { ...f, predictorBlacklistText: `${f.predictorBlacklistText}\nfoo` })).toBe(false);
  });
});

describe('notification settings', () => {
  it('sends blank secrets (keep) and clear flags only when asked', () => {
    const f = notifFromSettings(base);
    const p = buildNotificationParams(f);
    expect(p.get('notify_enabled')).toBe('on');
    expect(p.get('notify_grace_minutes')).toBe('5');
    expect(p.get('telegram_bot_token')).toBe('');
    expect(p.has('telegram_bot_token_clear')).toBe(false);
    expect(p.get('notify_telegram_min_severity')).toBe('critical');
    expect(p.get('teams_enabled')).toBe('1');
    expect(p.get('ntfy_server_url')).toBe('https://ntfy.sh');
    expect(p.has('ntfy_token_clear')).toBe(false);

    const cleared = buildNotificationParams({
      ...f, clearSecrets: ['telegram_bot_token'], teamsUrl: 'https://x', teamsUrlClear: true,
    });
    expect(cleared.get('telegram_bot_token_clear')).toBe('1');
    expect(cleared.get('teams_webhook_url')).toBe('');
    expect(cleared.get('teams_webhook_url_clear')).toBe('1');
  });

  it('builds the digest body with numbers', () => {
    expect(buildDigestBody(digestFromSettings(base))).toEqual({ digest_enabled: true, digest_day: 4, digest_hour: 9 });
  });
});

describe('ldap settings', () => {
  it('defaults the filter and TLS verification, and can force LDAP on for the test', () => {
    const f = ldapFromSettings(base);
    expect(f.ldapUserFilter).toContain('sAMAccountName');
    expect(f.ldapTlsVerify).toBe(true);
    expect(buildLdapFormData(f).get('ldap_enabled')).toBe('0');
    expect(buildLdapFormData(f, true).get('ldap_enabled')).toBe('1');
    expect(buildLdapFormData(f).get('ldap_tls_verify')).toBe('1');
    expect(ldapFromSettings({ ...base, ldap_tls_verify: '0' }).ldapTlsVerify).toBe(false);
  });
});
