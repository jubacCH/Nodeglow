'use client';

import { useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, Upload } from 'lucide-react';
import { BigNumber } from '@/components/ui/BigNumber';
import { Button } from '@/components/ui/Button';
import { Field, Input } from '@/components/ui/Field';
import { QueryState } from '@/components/ui/QueryState';
import { Skeleton } from '@/components/ui/Skeleton';
import { useConfirm } from '@/hooks/useConfirm';
import { apiErrorMessage, get, post } from '@/lib/api';
import { MIN_BACKUP_PASSPHRASE, isEncryptedBackup } from '@/lib/backup';
import { useToastStore } from '@/stores/toast';
import { DangerZone, SettingsSection } from './formKit';

interface BackupInfo {
  tables: Record<string, number>;
  total_rows: number;
  db_size: string;
}

export function BackupTab() {
  const toast = useToastStore();
  const qc = useQueryClient();
  const { confirm, ConfirmDialogElement } = useConfirm();
  const fileRef = useRef<HTMLInputElement>(null);

  const info = useQuery<BackupInfo>({
    queryKey: ['backup-info'],
    queryFn: () => get('/api/v1/backup/info'),
  });

  const [backupLoading, setBackupLoading] = useState(false);
  const [restoreLoading, setRestoreLoading] = useState(false);
  const [exportPassphrase, setExportPassphrase] = useState('');
  const [exportPassphrase2, setExportPassphrase2] = useState('');
  const [restorePassphrase, setRestorePassphrase] = useState('');

  const tooShort = exportPassphrase.length > 0 && exportPassphrase.length < MIN_BACKUP_PASSPHRASE;
  const mismatch = !!exportPassphrase2 && exportPassphrase !== exportPassphrase2;

  async function handleExport() {
    setBackupLoading(true);
    try {
      const envelope = await post<unknown>('/api/v1/backup', { passphrase: exportPassphrase });
      const blob = new Blob([JSON.stringify(envelope)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `nodeglow-backup-${new Date().toISOString().slice(0, 10)}.ngbackup.json`;
      a.click();
      URL.revokeObjectURL(url);
      setExportPassphrase('');
      setExportPassphrase2('');
      toast.show('Encrypted backup downloaded', 'success');
    } catch (err) {
      toast.show(apiErrorMessage(err, 'Backup failed'), 'error');
    } finally {
      setBackupLoading(false);
    }
  }

  async function handleFile(input: HTMLInputElement) {
    const file = input.files?.[0];
    if (!file) return;
    const reset = () => { input.value = ''; };
    let data: unknown;
    try {
      data = JSON.parse(await file.text());
    } catch {
      toast.show('This file is not a Nodeglow backup', 'error');
      reset();
      return;
    }
    const encrypted = isEncryptedBackup(data);
    if (encrypted && !restorePassphrase) {
      toast.show('This backup is encrypted — enter its passphrase first', 'error');
      reset();
      return;
    }
    const ok = await confirm({
      title: encrypted ? 'Restore Backup' : 'Restore UNENCRYPTED Backup',
      description: encrypted
        ? `Are you sure you want to restore from "${file.name}"? This will replace ALL existing data.`
        : `"${file.name}" is an unencrypted backup from an older version: it contains password hashes and `
          + 'credentials in plaintext. Restoring it will replace ALL existing data. Delete the file afterwards.',
      variant: 'danger',
      confirmLabel: encrypted ? 'Restore' : 'Restore unencrypted backup',
    });
    if (!ok) { reset(); return; }
    setRestoreLoading(true);
    try {
      // Through api(): it sends the CSRF token the backend requires.
      const result = await post<{ total_rows: number; warning?: string }>(
        '/api/v1/backup/restore',
        encrypted
          ? { backup: data, passphrase: restorePassphrase }
          : { backup: data, allow_unencrypted: true },
      );
      toast.show(`Restored ${result.total_rows} rows successfully`, 'success');
      if (result.warning) toast.show(result.warning, 'warning');
      setRestorePassphrase('');
      qc.invalidateQueries({ queryKey: ['backup-info'] });
    } catch (err) {
      toast.show(apiErrorMessage(err, 'Restore failed — check file format'), 'error');
    } finally {
      setRestoreLoading(false);
      reset();
    }
  }

  return (
    <div className="space-y-4">
      <SettingsSection id="db" title="Database">
        <QueryState
          query={info}
          compact
          loading={<div className="flex gap-8" aria-busy="true" aria-label="Loading"><Skeleton className="h-12 w-28" /><Skeleton className="h-12 w-28" /><Skeleton className="h-12 w-20" /></div>}
        >
          {(d) => (
            <div>
              <div className="mb-5 flex flex-wrap gap-x-10 gap-y-4">
                <BigNumber size="sm" value={d.total_rows.toLocaleString()} label="Total rows" />
                <BigNumber size="sm" value={d.db_size} label="Database size" />
                <BigNumber size="sm" value={Object.keys(d.tables).length} label="Tables" />
              </div>
              <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                {Object.entries(d.tables).map(([name, count]) => (
                  <li key={name} className="flex min-w-0 items-center justify-between gap-2 rounded-ng-sm border border-border bg-surface-2 px-3 py-1.5">
                    <span className="truncate font-mono text-meta text-fg-2">{name}</span>
                    <span className="num text-meta text-fg">{count.toLocaleString()}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </QueryState>
      </SettingsSection>

      <SettingsSection
        id="export"
        title="Export backup"
        description="A full backup of all PostgreSQL tables: hosts, agents, integrations, incidents, settings — also user password hashes and stored credentials, so the file is encrypted with a passphrase you choose. Without the passphrase the backup cannot be restored."
      >
        <div className="mb-4 grid max-w-xl gap-4 sm:grid-cols-2">
          <Field
            label="Passphrase"
            hint={`At least ${MIN_BACKUP_PASSPHRASE} characters.`}
            error={tooShort ? `At least ${MIN_BACKUP_PASSPHRASE} characters.` : undefined}
          >
            <Input id="backup-export-pass" type="password" autoComplete="new-password" value={exportPassphrase} onChange={(e) => setExportPassphrase(e.target.value)} />
          </Field>
          <Field label="Repeat passphrase" error={mismatch ? 'Passphrases do not match.' : undefined}>
            <Input id="backup-export-pass2" type="password" autoComplete="new-password" value={exportPassphrase2} onChange={(e) => setExportPassphrase2(e.target.value)} />
          </Field>
        </div>
        <Button
          size="sm"
          variant="secondary"
          loading={backupLoading}
          disabled={backupLoading || exportPassphrase.length < MIN_BACKUP_PASSPHRASE || exportPassphrase !== exportPassphrase2}
          onClick={handleExport}
        >
          {!backupLoading && <Download size={14} aria-hidden="true" />}
          {backupLoading ? 'Exporting…' : 'Download encrypted backup'}
        </Button>
      </SettingsSection>

      <DangerZone
        title="Restore backup"
        description="Restoring replaces ALL existing data and cannot be undone. Export a backup first. Encrypted backups need the passphrase they were created with."
      >
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Backup passphrase" className="w-full max-w-xs">
            <Input id="backup-restore-pass" type="password" autoComplete="off" value={restorePassphrase} onChange={(e) => setRestorePassphrase(e.target.value)} />
          </Field>
          <input
            ref={fileRef}
            type="file"
            accept=".json"
            id="backup-file"
            className="hidden"
            tabIndex={-1}
            aria-hidden="true"
            onChange={(e) => { void handleFile(e.target); }}
          />
          <Button variant="danger" size="md" loading={restoreLoading} disabled={restoreLoading} onClick={() => fileRef.current?.click()}>
            {!restoreLoading && <Upload size={14} aria-hidden="true" />}
            {restoreLoading ? 'Restoring…' : 'Choose file and restore…'}
          </Button>
        </div>
      </DangerZone>
      {ConfirmDialogElement}
    </div>
  );
}
