/** Alert rule form: defaults, operators and client-side validation. */
import type { AlertRule } from '@/types';

export const OPERATORS = [
  { key: 'gt', label: 'Greater than', symbol: '>' },
  { key: 'lt', label: 'Less than', symbol: '<' },
  { key: 'gte', label: 'Greater or equal', symbol: '≥' },
  { key: 'lte', label: 'Less or equal', symbol: '≤' },
  { key: 'eq', label: 'Equals', symbol: '=' },
  { key: 'ne', label: 'Not equals', symbol: '≠' },
  { key: 'contains', label: 'Contains', symbol: 'contains' },
  { key: 'not_contains', label: 'Does not contain', symbol: 'not contains' },
  { key: 'regex', label: 'Matches regex', symbol: '~' },
  { key: 'is_true', label: 'Is true', symbol: 'is true' },
  { key: 'is_false', label: 'Is false', symbol: 'is false' },
] as const;

export const SEVERITIES = ['critical', 'warning', 'info'] as const;
export const NOTIFY_CHANNELS = ['telegram', 'discord', 'webhook', 'email', 'teams', 'slack', 'ntfy'] as const;

const NUMERIC_OPS = new Set(['gt', 'lt', 'gte', 'lte']);
const NO_THRESHOLD_OPS = new Set(['is_true', 'is_false']);

export interface RuleFormState {
  name: string;
  source_type: string;
  source_id: string;
  field_path: string;
  operator: string;
  threshold: string;
  severity: string;
  cooldown_minutes: string;
  required_consecutive: string;
  notify_channels: string[];
  message_template: string;
}

export type RuleFormErrors = Partial<Record<keyof RuleFormState, string>>;

export const DEFAULT_RULE_FORM: RuleFormState = {
  name: '',
  source_type: '',
  source_id: '',
  field_path: '',
  operator: 'gt',
  threshold: '',
  severity: 'warning',
  cooldown_minutes: '5',
  required_consecutive: '2',
  notify_channels: [],
  message_template: '',
};

export function ruleToForm(rule: AlertRule): RuleFormState {
  return {
    name: rule.name,
    source_type: rule.source_type,
    source_id: rule.source_id != null ? String(rule.source_id) : '',
    field_path: rule.field_path,
    operator: rule.operator,
    threshold: rule.threshold ?? '',
    severity: rule.severity,
    cooldown_minutes: String(rule.cooldown_minutes),
    required_consecutive: String(rule.required_consecutive ?? 2),
    notify_channels: (rule.notify_channels ?? '').split(',').map((c) => c.trim()).filter(Boolean),
    message_template: rule.message_template ?? '',
  };
}

export function needsThreshold(operator: string) {
  return !NO_THRESHOLD_OPS.has(operator);
}

function isInt(v: string, min: number, max: number) {
  if (!/^\d+$/.test(v.trim())) return false;
  const n = Number(v);
  return n >= min && n <= max;
}

/** Errors by field; empty object = valid. The server stays the authority. */
export function validateRule(f: RuleFormState): RuleFormErrors {
  const e: RuleFormErrors = {};
  if (!f.name.trim()) e.name = 'Give the rule a name.';
  else if (f.name.trim().length > 200) e.name = 'At most 200 characters.';
  if (!f.source_type) e.source_type = 'Choose a source.';
  if (!f.field_path.trim()) e.field_path = 'Choose or enter the field to watch.';
  if (needsThreshold(f.operator)) {
    const t = f.threshold.trim();
    if (!t) e.threshold = 'Enter a threshold.';
    else if (NUMERIC_OPS.has(f.operator) && !Number.isFinite(Number(t))) e.threshold = 'This operator compares numbers.';
    else if (f.operator === 'regex') {
      try {
        new RegExp(t);
      } catch {
        e.threshold = 'Not a valid regular expression.';
      }
    }
  }
  if (!isInt(f.cooldown_minutes, 1, 10_080)) e.cooldown_minutes = 'Whole minutes, 1–10080.';
  if (!isInt(f.required_consecutive, 1, 10)) e.required_consecutive = 'A whole number, 1–10.';
  return e;
}

/** Form body for POST /rules/add and /rules/{id}/edit. */
export function ruleFormData(f: RuleFormState): FormData {
  const body = new FormData();
  body.set('name', f.name.trim());
  body.set('source_type', f.source_type);
  if (f.source_id) body.set('source_id', f.source_id);
  body.set('field_path', f.field_path.trim());
  body.set('operator', f.operator);
  body.set('threshold', needsThreshold(f.operator) ? f.threshold.trim() : '');
  body.set('severity', f.severity);
  body.set('cooldown_minutes', f.cooldown_minutes.trim() || '5');
  body.set('required_consecutive', f.required_consecutive.trim() || '2');
  if (f.notify_channels.length) body.set('notify_channels', f.notify_channels.join(','));
  if (f.message_template.trim()) body.set('message_template', f.message_template.trim());
  return body;
}

/** "cpu_pct > 90" for the rule list. */
export function conditionLabel(rule: Pick<AlertRule, 'field_path' | 'operator' | 'threshold'>): string {
  const op = OPERATORS.find((o) => o.key === rule.operator);
  const sym = op?.symbol ?? rule.operator;
  return needsThreshold(rule.operator) ? `${rule.field_path} ${sym} ${rule.threshold ?? ''}`.trim() : `${rule.field_path} ${sym}`;
}
