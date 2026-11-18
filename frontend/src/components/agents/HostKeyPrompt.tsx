'use client';

import { ShieldAlert } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import type { HostKeyQuestion } from '@/lib/agentDeploy';

/**
 * Asks the admin to confirm a host key before Nodeglow logs in for the first
 * time. Nothing is sent to the host until it is trusted.
 */
export function HostKeyPrompt({
  question, busy, onTrust, onCancel,
}: {
  question: HostKeyQuestion;
  busy?: boolean;
  onTrust: () => void;
  onCancel: () => void;
}) {
  const where = question.port === 22 ? question.host : `${question.host}:${question.port}`;
  return (
    <div role="alertdialog" aria-label="Confirm host key" className="space-y-3 rounded-ctl border border-warning/30 bg-warning-soft p-3">
      <div className="flex items-start gap-2">
        <ShieldAlert size={16} className="mt-0.5 shrink-0 text-warning" aria-hidden="true" />
        <p className="text-meta text-fg">
          Nodeglow has not connected to <span className="font-code">{where}</span> before. Compare its {question.keyType} host
          key with the one on the host before you trust it. Nothing has been sent to the host yet.
        </p>
      </div>
      <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded-ctl border border-border bg-surface p-2 font-code text-meta text-fg">
        {question.fingerprint}
      </pre>
      <p className="text-micro text-fg-3">
        On the host: <span className="font-code">ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub</span>
      </p>
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" size="sm" onClick={onTrust} disabled={busy}>
          {busy ? 'Starting…' : 'Trust and install'}
        </Button>
        <Button size="sm" onClick={onCancel} disabled={busy}>Cancel</Button>
      </div>
    </div>
  );
}
