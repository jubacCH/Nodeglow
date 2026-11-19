// Seals a deploy login to a probe's public key so the server only ever stores ciphertext.
// HPKE base mode: DHKEM(X25519, HKDF-SHA256) + HKDF-SHA256 + ChaCha20-Poly1305.
import { CipherSuite, HkdfSha256 } from '@hpke/core';
import { Chacha20Poly1305 } from '@hpke/chacha20poly1305';
import { DhkemX25519HkdfSha256 } from '@hpke/dhkem-x25519';

export const SEAL_SUITE = 'hpke-x25519-sha256-chacha20poly1305';

const INFO = new TextEncoder().encode('nodeglow sealed credential v1');
const KEY_BYTES = 32;
const MAX_PLAINTEXT_BYTES = 16384;

export interface LoginPlaintext {
  v: 1;
  id: string;
  name: string;
  username: string;
  password: string | null;
  private_key: string | null;
  passphrase: string | null;
  hosts: string[];
}

export interface Envelope {
  v: 1;
  suite: string;
  key: string;
  enc: string;
  ct: string;
}

function toBase64(bytes: Uint8Array): string {
  let s = '';
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s);
}

function publicKeyBytes(publicKeyB64: string): Uint8Array {
  let raw: string;
  try {
    raw = atob(publicKeyB64);
  } catch {
    throw new Error('Probe key is not valid base64');
  }
  if (raw.length !== KEY_BYTES) throw new Error('Probe key must be 32 bytes');
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

/** "SHA256:" + unpadded base64 of SHA-256 over the raw 32-byte public key. */
export async function sealFingerprint(publicKeyB64: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', publicKeyBytes(publicKeyB64) as BufferSource);
  return 'SHA256:' + toBase64(new Uint8Array(digest)).replace(/=+$/, '');
}

export async function sealLogin(publicKeyB64: string, login: LoginPlaintext): Promise<Envelope> {
  const keyBytes = publicKeyBytes(publicKeyB64);
  const plaintext = new TextEncoder().encode(JSON.stringify(login));
  if (plaintext.length > MAX_PLAINTEXT_BYTES) throw new Error('Login is too large to seal');

  const key = await sealFingerprint(publicKeyB64);
  const suite = new CipherSuite({
    kem: new DhkemX25519HkdfSha256(),
    kdf: new HkdfSha256(),
    aead: new Chacha20Poly1305(),
  });
  const recipientPublicKey = await suite.kem.deserializePublicKey(keyBytes as unknown as ArrayBuffer);
  const sender = await suite.createSenderContext({ recipientPublicKey, info: INFO });
  const ct = await sender.seal(plaintext, new TextEncoder().encode(key));
  return {
    v: 1,
    suite: SEAL_SUITE,
    key,
    enc: toBase64(new Uint8Array(sender.enc)),
    ct: toBase64(new Uint8Array(ct)),
  };
}
