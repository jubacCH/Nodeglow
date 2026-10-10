'use client';

import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { AlertTriangle, Check, ChevronDown, Circle } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Card, CardHeader } from '@/components/ui/Card';
import { formatAsOf } from '@/components/ui/QueryState';
import { cn } from '@/lib/utils';
import { sameValues } from './settingsForm';

/**
 * Local building blocks for the settings tabs (not general enough for
 * components/ui): a form-state hook with dirty tracking, a save bar that
 * always says where the form stands (unsaved / saving / saved / failed),
 * section cards and a danger zone.
 */

/* ---------- Form state ---------- */

export interface SectionForm<T> {
  value: T | null;
  set: <K extends keyof T>(key: K, v: T[K]) => void;
  patch: (p: Partial<T>) => void;
  dirty: boolean;
  discard: () => void;
  /** Call after a successful save: the next server copy replaces the form
   *  (clears write-only secret inputs). */
  markSaved: () => void;
}

/**
 * Form values for one settings section, seeded from the server copy. When the
 * server copy changes (refetch after any save), a section with unsaved edits
 * keeps them; clean or just-saved sections take the new values. The old page
 * reset every tab on every save and silently dropped edits in other tabs.
 */
export function useSectionForm<S, T extends object>(
  source: S | undefined,
  /** TanStack `dataUpdatedAt`: changes on every successful fetch. */
  updatedAt: number,
  map: (s: S) => T,
  equals: (a: T, b: T) => boolean = sameValues,
): SectionForm<T> {
  const [state, setState] = useState<{ value: T; baseline: T } | null>(null);
  const resetNext = useRef(true);
  const mapRef = useRef(map);
  mapRef.current = map;
  const equalsRef = useRef(equals);
  equalsRef.current = equals;

  useEffect(() => {
    if (!source) return;
    const fresh = mapRef.current(source);
    const reset = resetNext.current;
    resetNext.current = false;
    setState((prev) => {
      const keep = prev && !reset && !equalsRef.current(prev.value, prev.baseline);
      return { value: keep ? prev.value : fresh, baseline: fresh };
    });
    // `source` identity changes with every render of the query result; the
    // fetch timestamp is the real change signal.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [updatedAt, !!source]);

  const set = useCallback(<K extends keyof T>(key: K, v: T[K]) => {
    setState((p) => (p ? { ...p, value: { ...p.value, [key]: v } } : p));
  }, []);
  const patch = useCallback((part: Partial<T>) => {
    setState((p) => (p ? { ...p, value: { ...p.value, ...part } } : p));
  }, []);
  const discard = useCallback(() => setState((p) => (p ? { ...p, value: p.baseline } : p)), []);
  const markSaved = useCallback(() => { resetNext.current = true; }, []);

  return {
    value: state?.value ?? null,
    set,
    patch,
    dirty: state ? !equals(state.value, state.baseline) : false,
    discard,
    markSaved,
  };
}

/* ---------- Save state ---------- */

export type SaveStatus =
  | { kind: 'clean' }
  | { kind: 'dirty' }
  | { kind: 'saving' }
  | { kind: 'saved'; at: number }
  | { kind: 'error'; message: string };

/** Derives the save status of a section from its dirty flag and the last save attempt. */
export function useSaveStatus(dirty: boolean) {
  const [last, setLast] = useState<{ ok: true; at: number } | { ok: false; message: string } | null>(null);
  const [saving, setSaving] = useState(false);

  const status: SaveStatus = saving
    ? { kind: 'saving' }
    : last && !last.ok && dirty
      ? { kind: 'error', message: last.message }
      : dirty
        ? { kind: 'dirty' }
        : last?.ok
          ? { kind: 'saved', at: last.at }
          : { kind: 'clean' };

  return {
    status,
    start: useCallback(() => { setSaving(true); }, []),
    succeed: useCallback(() => { setSaving(false); setLast({ ok: true, at: Date.now() }); }, []),
    fail: useCallback((message: string) => { setSaving(false); setLast({ ok: false, message }); }, []),
  };
}

function StatusText({ status }: { status: SaveStatus }) {
  switch (status.kind) {
    case 'dirty':
      return (
        <span className="inline-flex items-center gap-1.5 text-degraded">
          <Circle size={8} className="fill-current" aria-hidden="true" /> Unsaved changes
        </span>
      );
    case 'saving':
      return <span className="text-fg-2">Saving…</span>;
    case 'saved':
      return (
        <span className="inline-flex items-center gap-1.5 text-ok">
          <Check size={14} aria-hidden="true" /> Saved {formatAsOf(status.at)}
        </span>
      );
    case 'error':
      return (
        <span className="inline-flex min-w-0 items-center gap-1.5 text-down">
          <AlertTriangle size={14} aria-hidden="true" className="shrink-0" />
          <span className="truncate">Not saved: {status.message}</span>
        </span>
      );
    default:
      return <span className="text-fg-3">All changes saved</span>;
  }
}

/**
 * Sticky bar at the end of a form: status on the left, Discard + Save on the
 * right. Sits above the mobile tab bar.
 */
export function SaveBar({
  status, onSave, onDiscard, label = 'Save changes', note, extra, sticky = true,
}: {
  status: SaveStatus;
  onSave: () => void;
  onDiscard?: () => void;
  label?: string;
  /** Short scope hint, e.g. "Saves System and Monitoring". */
  note?: ReactNode;
  /** Additional buttons left of Save (e.g. "Test summary"). */
  extra?: ReactNode;
  sticky?: boolean;
}) {
  const dirty = status.kind === 'dirty' || status.kind === 'error';
  return (
    <div
      className={cn(
        'z-[5] mt-4 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-card border border-border bg-surface px-4 py-3',
        sticky && 'sticky bottom-3 max-[759px]:bottom-[calc(var(--ng-tabbar-h)+12px)]',
      )}
    >
      <div className="min-w-0 text-ui" role="status" aria-live="polite">
        <StatusText status={status} />
        {note && <p className="text-meta text-fg-3">{note}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {extra}
        {onDiscard && dirty && (
          <Button variant="ghost" size="sm" onClick={onDiscard}>Discard</Button>
        )}
        <Button size="sm" onClick={onSave} loading={status.kind === 'saving'} disabled={!dirty}>
          {label}
        </Button>
      </div>
    </div>
  );
}

/* ---------- Layout ---------- */

/** Card with a title, one-line description and optional header actions. */
export function SettingsSection({
  title, description, actions, children, className, id,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
  id?: string;
}) {
  const headingId = id ? `${id}-title` : undefined;
  return (
    <Card as="section" aria-labelledby={headingId} className={className} id={id}>
      <CardHeader title={title} titleId={headingId} actions={actions} className={description ? 'mb-1' : undefined} />
      {description && <p className="mb-4 max-w-3xl text-ui text-fg-2">{description}</p>}
      {children}
    </Card>
  );
}

/** Destructive actions, visually separated (tinted border, no glow). */
export function DangerZone({ title = 'Danger zone', description, children }: { title?: string; description?: ReactNode; children: ReactNode }) {
  return (
    <Card as="section" aria-label={title} className="border-down/40">
      <h2 className="flex items-center gap-2 text-body font-medium text-down">
        <AlertTriangle size={16} aria-hidden="true" /> {title}
      </h2>
      {description && <p className="mb-4 mt-1 max-w-3xl text-ui text-fg-2">{description}</p>}
      {children}
    </Card>
  );
}

/** Collapsible numbered setup instructions for a channel. */
export function SetupGuide({ steps }: { steps: ReactNode[] }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div className="mb-4">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen(!open)}
        className="inline-flex items-center gap-1.5 rounded-chip text-meta font-medium text-accent hover:text-accent-hover"
      >
        <ChevronDown size={14} aria-hidden="true" className={cn('transition-transform', !open && '-rotate-90')} />
        Setup guide
      </button>
      {open && (
        <ol id={id} className="ml-1 mt-2 list-inside list-decimal space-y-1.5 text-meta leading-relaxed text-fg-2">
          {steps.map((step, i) => <li key={i}>{step}</li>)}
        </ol>
      )}
    </div>
  );
}

/** Inline code chip used in help texts. */
export function Code({ children }: { children: ReactNode }) {
  return <code className="rounded-chip bg-surface-2 px-1.5 py-0.5 font-mono text-meta text-fg">{children}</code>;
}

/** Notice box (info / warning / success / error) for inline results. */
export function Notice({ tone, children, className }: { tone: 'info' | 'warning' | 'ok' | 'down'; children: ReactNode; className?: string }) {
  const tones = {
    info: 'border-border-2 bg-surface-2 text-fg-2',
    warning: 'border-warning/30 bg-warning-soft text-warning',
    ok: 'border-ok/30 bg-ok-soft text-ok',
    down: 'border-down/30 bg-down-soft text-down',
  } as const;
  return (
    <div role={tone === 'down' ? 'alert' : 'status'} className={cn('rounded-ctl border px-3 py-2 text-ui', tones[tone], className)}>
      {children}
    </div>
  );
}
