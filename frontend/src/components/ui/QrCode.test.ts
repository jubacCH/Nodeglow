import { describe, expect, it } from 'vitest';
import { qrModules } from './QrCode';

describe('QR code', () => {
  const uri = 'otpauth://totp/Nodeglow%3Aadmin?secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP&issuer=Nodeglow&algorithm=SHA1&digits=6&period=30';

  it('encodes an otpauth URI into a square of modules', () => {
    const { path, count } = qrModules(uri);
    expect(count).toBeGreaterThanOrEqual(21); // version 1 is 21 x 21
    expect((count - 17) % 4).toBe(0); // every QR version is 17 + 4n wide
    expect(path.startsWith('M0 0h1v1h-1z')).toBe(true); // finder pattern corner
  });

  it('gives different codes for different secrets', () => {
    expect(qrModules(uri).path).not.toBe(qrModules(uri.replace('JBSW', 'KBSW')).path);
  });
});
