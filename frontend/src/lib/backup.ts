/** Helpers for the passphrase-encrypted database backup (settings → Backup). */

/** Must match MIN_PASSPHRASE_LENGTH in backend/services/backup.py. */
export const MIN_BACKUP_PASSPHRASE = 12;

/** Magic `format` value of the encrypted envelope the backend exports. */
export const ENCRYPTED_BACKUP_FORMAT = 'nodeglow-backup-encrypted';

/** True for an encrypted export; false for an old plaintext one (or junk). */
export function isEncryptedBackup(data: unknown): boolean {
  return (
    typeof data === 'object'
    && data !== null
    && (data as { format?: unknown }).format === ENCRYPTED_BACKUP_FORMAT
  );
}
