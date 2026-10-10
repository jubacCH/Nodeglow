import { describe, expect, it } from 'vitest';
import { isEncryptedBackup } from './backup';

describe('isEncryptedBackup', () => {
  it('recognises the encrypted envelope', () => {
    expect(isEncryptedBackup({ format: 'nodeglow-backup-encrypted', version: 1 })).toBe(true);
  });

  it('treats old plaintext exports and junk as not encrypted', () => {
    expect(isEncryptedBackup({ _meta: { format: 'nodeglow-backup' }, tables: {} })).toBe(false);
    expect(isEncryptedBackup(null)).toBe(false);
    expect(isEncryptedBackup('nodeglow-backup-encrypted')).toBe(false);
    expect(isEncryptedBackup([])).toBe(false);
  });
});
