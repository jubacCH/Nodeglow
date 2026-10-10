'use client';

import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { Checkbox, Field, Input, Select } from '@/components/ui/Field';
import { useAgents } from '@/hooks/queries/useAgents';
import { apiErrorMessage, patch, post } from '@/lib/api';
import {
  DEFAULT_HTTP_FORM, hasHttpCheck, httpFormFromOptions, httpOptionsPayload, validateHttpForm,
} from '@/lib/httpOptions';
import type { HttpOptions } from '@/types';
import { HttpOptionsFields } from './HttpOptionsFields';
import {
  CHECK_TYPES, EMPTY_HOST_FORM, hasErrors, validateHostForm,
  type HostFormErrors, type HostFormValues,
} from './hostForm';
import { probeOptionLabel, type ProbeAgent } from './probes';

/** Fields of an existing host the edit form starts from. */
export interface EditableHost {
  id: number;
  name: string;
  hostname: string;
  check_type: string;
  latency_threshold_ms: number | null;
  enabled: boolean;
  probe_id?: number | null;
  http_options?: Partial<HttpOptions> | null;
}

interface HostFormModalProps {
  open: boolean;
  onClose: () => void;
  mode: 'add' | 'edit';
  host?: EditableHost;
  /** Called with the host id after a successful save. */
  onSaved: (id: number | null) => void;
}

/** Add or edit a host. Every control is labelled; errors sit under their field. */
export function HostFormModal({ open, onClose, mode, host, onSaved }: HostFormModalProps) {
  const { data: agents } = useAgents();
  const probes = ((agents ?? []) as ProbeAgent[]).filter((a) => a.is_probe);
  const [form, setForm] = useState<HostFormValues>(EMPTY_HOST_FORM);
  const [httpForm, setHttpForm] = useState(DEFAULT_HTTP_FORM);
  const [errors, setErrors] = useState<HostFormErrors>({});
  const [httpError, setHttpError] = useState<string | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Initialise only when the dialog opens: live updates of `host` while it is
  // open must not overwrite what the user is typing.
  const wasOpen = useRef(false);
  useEffect(() => {
    const opening = open && !wasOpen.current;
    wasOpen.current = open;
    if (!opening) return;
    setErrors({});
    setHttpError(null);
    setServerError(null);
    if (mode === 'edit' && host) {
      setForm({
        ...EMPTY_HOST_FORM,
        name: host.name ?? '',
        hostname: host.hostname ?? '',
        check_type: host.check_type ?? 'icmp',
        latency_threshold_ms: host.latency_threshold_ms ? String(host.latency_threshold_ms) : '',
        enabled: host.enabled !== false,
        probe_id: host.probe_id != null ? String(host.probe_id) : '',
      });
      setHttpForm(httpFormFromOptions(host.http_options));
    } else {
      setForm(EMPTY_HOST_FORM);
      setHttpForm(DEFAULT_HTTP_FORM);
    }
  }, [open, mode, host]);

  const isHttp = hasHttpCheck(form.check_type);
  const set = <K extends keyof HostFormValues>(key: K, value: HostFormValues[K]) => {
    setForm((f) => ({ ...f, [key]: value }));
    if (errors[key]) setErrors((e) => ({ ...e, [key]: undefined }));
  };

  async function submit(ev: FormEvent) {
    ev.preventDefault();
    const fieldErrors = validateHostForm(form, mode);
    const hErr = isHttp ? validateHttpForm(httpForm) : null;
    setErrors(fieldErrors);
    setHttpError(hErr);
    if (hasErrors(fieldErrors) || hErr) return;
    setServerError(null);
    setSaving(true);
    try {
      if (mode === 'add') {
        const res = await post<{ ok: boolean; id?: number }>('/hosts/api/create', {
          name: form.name.trim(),
          hostname: form.hostname.trim(),
          check_type: form.check_type,
          port: form.port.trim() || undefined,
          http_options: isHttp ? httpOptionsPayload(httpForm) : undefined,
        });
        onSaved(res?.id ?? null);
      } else if (host) {
        await patch(`/api/v1/hosts/${host.id}`, {
          name: form.name.trim(),
          hostname: form.hostname.trim(),
          latency_threshold_ms: form.latency_threshold_ms.trim() ? Number(form.latency_threshold_ms) : null,
          enabled: form.enabled,
          probe_id: form.probe_id ? Number(form.probe_id) : null,
          ...(isHttp ? { http_options: httpOptionsPayload(httpForm) } : {}),
        });
        onSaved(host.id);
      }
      onClose();
    } catch (e) {
      setServerError(apiErrorMessage(e, mode === 'add' ? 'Could not add the host.' : 'Could not save the host.'));
    } finally {
      setSaving(false);
    }
  }

  const formId = mode === 'add' ? 'host-add-form' : 'host-edit-form';
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={mode === 'add' ? 'Add host' : 'Edit host'}
      description={mode === 'add' ? 'Nodeglow starts checking the host right after it is added.' : undefined}
      size="lg"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button type="submit" form={formId} loading={saving}>
            {mode === 'add' ? 'Add host' : 'Save changes'}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} noValidate className="space-y-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Name" required error={errors.name}>
            <Input type="text" autoComplete="off" placeholder="File server" value={form.name} onChange={(e) => set('name', e.target.value)} />
          </Field>
          <Field label="Hostname or IP" required error={errors.hostname} hint="FQDN or IP address, without protocol">
            <Input
              type="text"
              autoComplete="off"
              spellCheck={false}
              className="font-mono"
              placeholder="server.example.com"
              value={form.hostname}
              onChange={(e) => set('hostname', e.target.value)}
            />
          </Field>
          {mode === 'add' && (
            <>
              <Field label="Check type">
                <Select value={form.check_type} onChange={(e) => set('check_type', e.target.value)}>
                  {CHECK_TYPES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                </Select>
              </Field>
              <Field label="Port" required={form.check_type === 'tcp'} error={errors.port} hint="Optional for HTTP(S)">
                <Input type="text" inputMode="numeric" placeholder="443" value={form.port} onChange={(e) => set('port', e.target.value)} />
              </Field>
            </>
          )}
          {mode === 'edit' && (
            <>
              <Field label="Latency threshold (ms)" error={errors.latency_threshold_ms} hint="Above this the host counts as degraded. Empty = global default.">
                <Input
                  type="text"
                  inputMode="numeric"
                  placeholder="200"
                  value={form.latency_threshold_ms}
                  onChange={(e) => set('latency_threshold_ms', e.target.value)}
                />
              </Field>
              <Field label="Checked by" hint="Assign a probe for hosts on a network the core cannot reach.">
                <Select value={form.probe_id} onChange={(e) => set('probe_id', e.target.value)}>
                  <option value="">Core (direct)</option>
                  {probes.map((p) => <option key={p.id} value={p.id}>{probeOptionLabel(p)}</option>)}
                </Select>
              </Field>
              <Checkbox
                className="sm:col-span-2"
                label="Monitoring enabled"
                description="When off, the host is not checked and shows as disabled."
                checked={form.enabled}
                onChange={(e) => set('enabled', e.target.checked)}
              />
            </>
          )}
        </div>
        {isHttp && (
          <details className="rounded-ctl border border-border p-3" open={!!httpError}>
            <summary className="cursor-pointer text-ui font-medium text-fg">HTTP check options</summary>
            <div className="mt-3">
              <HttpOptionsFields value={httpForm} onChange={(v) => { setHttpForm(v); setHttpError(null); }} />
            </div>
            {form.probe_id && (
              <p className="mt-2 text-meta text-fg-3">Probes run the plain HTTP check; these options apply when the core checks the host.</p>
            )}
            {httpError && <p role="alert" className="mt-2 text-meta text-down">{httpError}</p>}
          </details>
        )}
        {serverError && (
          <p role="alert" className="rounded-ctl border border-down/30 bg-down-soft px-3 py-2 text-ui text-down">{serverError}</p>
        )}
      </form>
    </Modal>
  );
}
