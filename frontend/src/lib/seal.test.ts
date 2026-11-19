// @vitest-environment node
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { CipherSuite, HkdfSha256 } from '@hpke/core';
import { Chacha20Poly1305 } from '@hpke/chacha20poly1305';
import { DhkemX25519HkdfSha256 } from '@hpke/dhkem-x25519';
import { describe, expect, it } from 'vitest';
import { SEAL_SUITE, sealFingerprint, sealLogin, type Envelope, type LoginPlaintext } from './seal';

const FIXTURE = resolve(__dirname, '../../../agent/tests/fixtures/sealed_v1.json');
const INFO = new TextEncoder().encode('nodeglow sealed credential v1');

const suite = new CipherSuite({
  kem: new DhkemX25519HkdfSha256(),
  kdf: new HkdfSha256(),
  aead: new Chacha20Poly1305(),
});

const b64 = (b: Uint8Array) => Buffer.from(b).toString('base64');
const unb64 = (s: string) => new Uint8Array(Buffer.from(s, 'base64'));

const login: LoginPlaintext = {
  v: 1,
  id: 'c0ffee00-1111-2222-3333-444455556666',
  name: 'lab-root',
  username: 'root',
  password: 'correct horse battery staple',
  private_key: null,
  passphrase: null,
  hosts: ['10.0.20.0/24', '*.lab.example'],
};

async function newKeyPair() {
  const kp = await suite.kem.generateKeyPair();
  const pub = new Uint8Array(await suite.kem.serializePublicKey(kp.publicKey));
  const priv = new Uint8Array(await suite.kem.serializePrivateKey(kp.privateKey));
  return { kp, pub, priv };
}

async function open(env: Envelope, priv: Uint8Array, aad?: string): Promise<LoginPlaintext> {
  const recipientKey = await suite.kem.deserializePrivateKey(priv);
  const ctx = await suite.createRecipientContext({ recipientKey, enc: unb64(env.enc), info: INFO });
  const pt = await ctx.open(unb64(env.ct), new TextEncoder().encode(aad ?? env.key));
  return JSON.parse(new TextDecoder().decode(pt));
}

describe('sealFingerprint', () => {
  it('is SHA256: plus unpadded base64 of the digest over the raw key', async () => {
    const raw = new Uint8Array(32).map((_, i) => i + 1);
    const expected = 'SHA256:' + createHash('sha256').update(raw).digest('base64').replace(/=+$/, '');
    expect(await sealFingerprint(b64(raw))).toBe(expected);
    expect(expected).not.toContain('=');
  });

  it('rejects a key that is not 32 bytes', async () => {
    await expect(sealFingerprint(b64(new Uint8Array(31)))).rejects.toThrow();
  });
});

describe('sealLogin', () => {
  it('round-trips through HPKE with the fingerprint as aad', async () => {
    const { pub, priv } = await newKeyPair();
    const env = await sealLogin(b64(pub), login);
    expect(env.v).toBe(1);
    expect(env.suite).toBe(SEAL_SUITE);
    expect(env.key).toBe(await sealFingerprint(b64(pub)));
    expect(await open(env, priv)).toEqual(login);
  });

  it('does not open with a different aad', async () => {
    const { pub, priv } = await newKeyPair();
    const env = await sealLogin(b64(pub), login);
    await expect(open(env, priv, 'SHA256:other')).rejects.toThrow();
  });

  it('does not open with a different private key', async () => {
    const { pub } = await newKeyPair();
    const other = await newKeyPair();
    const env = await sealLogin(b64(pub), login);
    await expect(open(env, other.priv)).rejects.toThrow();
  });

  it('rejects a recipient key that is not 32 bytes', async () => {
    await expect(sealLogin(b64(new Uint8Array(16)), login)).rejects.toThrow();
  });

  it('rejects a plaintext over 16384 bytes', async () => {
    const { pub } = await newKeyPair();
    await expect(sealLogin(b64(pub), { ...login, password: 'x'.repeat(17000) })).rejects.toThrow();
  });
});

describe('interop fixture', () => {
  it.skipIf(process.env.WRITE_SEAL_FIXTURE !== '1')('writes the fixture', async () => {
    const { pub, priv } = await newKeyPair();
    const envelope = await sealLogin(b64(pub), login);
    const fixture = { private_key: b64(priv), public_key: b64(pub), plaintext: login, envelope };
    writeFileSync(FIXTURE, JSON.stringify(fixture, null, 2) + '\n');
  });

  it('opens to its plaintext', async () => {
    const f = JSON.parse(readFileSync(FIXTURE, 'utf8'));
    expect(unb64(f.private_key)).toHaveLength(32);
    expect(f.envelope.key).toBe(await sealFingerprint(f.public_key));
    expect(await open(f.envelope, unb64(f.private_key))).toEqual(f.plaintext);
    expect(f.plaintext.hosts).toEqual(['10.0.20.0/24', '*.lab.example']);
  });
});
