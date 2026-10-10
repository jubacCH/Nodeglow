import { describe, expect, it } from 'vitest';
import { DEFAULT_RULE_FORM, conditionLabel, ruleFormData, ruleToForm, validateRule } from './rules';
import type { AlertRule } from '@/types';

const valid = { ...DEFAULT_RULE_FORM, name: 'High CPU', source_type: 'proxmox', field_path: 'cpu_pct', threshold: '90' };

describe('validateRule', () => {
  it('accepts a complete rule', () => {
    expect(validateRule(valid)).toEqual({});
  });

  it('requires name, source and field', () => {
    const e = validateRule(DEFAULT_RULE_FORM);
    expect(Object.keys(e).sort()).toEqual(['field_path', 'name', 'source_type', 'threshold']);
  });

  it('checks the threshold against the operator', () => {
    expect(validateRule({ ...valid, threshold: 'abc' }).threshold).toMatch(/numbers/);
    expect(validateRule({ ...valid, operator: 'contains', threshold: 'abc' }).threshold).toBeUndefined();
    expect(validateRule({ ...valid, operator: 'regex', threshold: '([' }).threshold).toMatch(/regular/);
    expect(validateRule({ ...valid, operator: 'is_true', threshold: '' }).threshold).toBeUndefined();
  });

  it('bounds cooldown and consecutive matches', () => {
    expect(validateRule({ ...valid, cooldown_minutes: '0' }).cooldown_minutes).toBeDefined();
    expect(validateRule({ ...valid, required_consecutive: '11' }).required_consecutive).toBeDefined();
    expect(validateRule({ ...valid, required_consecutive: '1.5' }).required_consecutive).toBeDefined();
  });
});

describe('rule form round trip', () => {
  const rule: AlertRule = {
    id: 1, name: 'Disk', enabled: true, source_type: 'agent', source_id: 4, field_path: 'disk_pct',
    operator: 'gte', threshold: '85', severity: 'critical', notify_channels: 'email, ntfy',
    message_template: null, cooldown_minutes: 10, required_consecutive: 3, last_triggered_at: null, created_at: '',
  };

  it('splits channels and serialises the form', () => {
    const f = ruleToForm(rule);
    expect(f.notify_channels).toEqual(['email', 'ntfy']);
    const body = ruleFormData(f);
    expect(body.get('notify_channels')).toBe('email,ntfy');
    expect(body.get('source_id')).toBe('4');
    expect(body.get('message_template')).toBeNull();
  });

  it('drops the threshold for boolean operators', () => {
    expect(ruleFormData({ ...ruleToForm(rule), operator: 'is_true' }).get('threshold')).toBe('');
    expect(conditionLabel({ field_path: 'up', operator: 'is_false', threshold: '1' })).toBe('up is false');
    expect(conditionLabel(rule)).toBe('disk_pct ≥ 85');
  });
});
