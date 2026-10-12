#!/bin/sh
# Release signing of agent binaries (spec 2026-10-11 rule 4, P2a).
#
#   sign.sh pubkey <key.pem>                      print "<public key hex> <key id>"
#   sign.sh sign   <key.pem> <platform> <binary>  write <binary>.sig
#   sign.sh verify <key.pem> <binary>             check <binary>.sig with the key
#
# <key.pem>: Ed25519 private key, PKCS#8 PEM ("-----BEGIN PRIVATE KEY-----"),
# as made by `openssl genpkey -algorithm ed25519`. Needs OpenSSL 3 (-rawin).
# `verify` also takes the public key PEM ("-----BEGIN PUBLIC KEY-----").
#
# The signed message is  nodeglow-agent-binary:v1:<platform>:<sha256 hex>
# (pure Ed25519, no prehash) — must match release_message() in
# agent/src/updater.rs and backend/services/agent_release.py. <platform> is
# "linux" or "windows", the platform name the agent asks the server for.
#
# <binary>.sig is one line of JSON:
#   {"format":"nodeglow-agent-signature-v1","alg":"ed25519","platform":"linux",
#    "key_id":"<16 hex>","sha256":"<64 hex>","signature":"<128 hex>"}
# key_id = first 8 bytes of SHA-256(raw 32-byte public key), hex.
set -eu

hex() { od -An -v -tx1 | tr -d ' \n'; }

is_private() { grep -q "BEGIN PRIVATE KEY" "$1"; }

check_key() {
    if is_private "$1"; then
        openssl pkey -in "$1" -noout -text 2>/dev/null | head -n 1 | grep -q "ED25519 Private-Key" && return 0
    elif [ "${2:-}" = "public-ok" ]; then
        openssl pkey -pubin -in "$1" -noout -text 2>/dev/null | head -n 1 | grep -q "ED25519 Public-Key" && return 0
    fi
    echo "sign.sh: $1 is not an Ed25519 key in PEM (PKCS#8 / SPKI)" >&2
    exit 2
}

pub_pem() {  # pub_pem <key> <out>: public key PEM of a private or public key
    if is_private "$1"; then
        openssl pkey -in "$1" -pubout -out "$2"
    else
        cp "$1" "$2"
    fi
}

raw_pub() {
    if is_private "$1"; then
        openssl pkey -in "$1" -pubout -outform DER | tail -c 32
    else
        openssl pkey -pubin -in "$1" -outform DER | tail -c 32
    fi
}
pub_hex() { raw_pub "$1" | hex; }
key_id() { raw_pub "$1" | openssl dgst -sha256 -binary | head -c 8 | hex; }

field() {  # field <name> <file>: value of a string field of the one-line JSON
    sed -n "s/.*\"$1\":\"\([^\"]*\)\".*/\1/p" "$2"
}

cmd=${1:-}
case "$cmd" in
    pubkey)
        [ $# -eq 2 ] || { echo "usage: sign.sh pubkey <key.pem>" >&2; exit 2; }
        check_key "$2" public-ok
        echo "$(pub_hex "$2") $(key_id "$2")"
        ;;
    sign)
        [ $# -eq 4 ] || { echo "usage: sign.sh sign <key.pem> <platform> <binary>" >&2; exit 2; }
        key=$2; platform=$3; bin=$4
        case "$platform" in linux|windows) ;; *) echo "sign.sh: platform must be linux or windows" >&2; exit 2;; esac
        check_key "$key"
        tmp=$(mktemp -d)
        trap 'rm -rf "$tmp"' EXIT
        sha=$(openssl dgst -sha256 -binary "$bin" | hex)
        printf '%s' "nodeglow-agent-binary:v1:$platform:$sha" > "$tmp/msg"
        openssl pkeyutl -sign -inkey "$key" -rawin -in "$tmp/msg" -out "$tmp/sig"
        pub_pem "$key" "$tmp/pub.pem"
        openssl pkeyutl -verify -pubin -inkey "$tmp/pub.pem" -rawin -in "$tmp/msg" -sigfile "$tmp/sig" >/dev/null
        sig=$(hex < "$tmp/sig")
        kid=$(key_id "$key")
        printf '{"format":"nodeglow-agent-signature-v1","alg":"ed25519","platform":"%s","key_id":"%s","sha256":"%s","signature":"%s"}\n' \
            "$platform" "$kid" "$sha" "$sig" > "$bin.sig"
        echo "signed $bin ($platform, key $kid, sha256 $sha)"
        ;;
    verify)
        [ $# -eq 3 ] || { echo "usage: sign.sh verify <key.pem> <binary>" >&2; exit 2; }
        key=$2; bin=$3
        check_key "$key" public-ok
        tmp=$(mktemp -d)
        trap 'rm -rf "$tmp"' EXIT
        sha=$(openssl dgst -sha256 -binary "$bin" | hex)
        [ "$(field sha256 "$bin.sig")" = "$sha" ] || { echo "sign.sh: $bin.sig is for other bytes" >&2; exit 1; }
        [ "$(field key_id "$bin.sig")" = "$(key_id "$key")" ] || { echo "sign.sh: $bin.sig names another key" >&2; exit 1; }
        printf '%s' "nodeglow-agent-binary:v1:$(field platform "$bin.sig"):$sha" > "$tmp/msg"
        field signature "$bin.sig" | xxd -r -p > "$tmp/sig"
        pub_pem "$key" "$tmp/pub.pem"
        openssl pkeyutl -verify -pubin -inkey "$tmp/pub.pem" -rawin -in "$tmp/msg" -sigfile "$tmp/sig"
        ;;
    *)
        echo "usage: sign.sh pubkey|sign|verify ..." >&2
        exit 2
        ;;
esac
