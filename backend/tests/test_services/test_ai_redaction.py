"""Redaction of personal data before anything is sent to an AI provider.

The lines below are shaped like what Nodeglow's syslog actually receives
(sshd/PAM, pfSense filterlog, Windows Security events, nginx access logs),
because those are the examples ai_context forwards as "top errors" and
incident syslog patterns.
"""
import re

from services.ai_redaction import PLACEHOLDER_RE, Redactor


def _r(**kw) -> Redactor:
    return Redactor(**kw)


# ── sshd / PAM ────────────────────────────────────────────────────────────────

def test_sshd_failed_password_invalid_user():
    r = _r()
    out = r.redact(
        "Oct 10 13:55:36 bastion sshd[1234]: Failed password for invalid user jsmith "
        "from 203.0.113.45 port 51234 ssh2"
    )
    assert "jsmith" not in out and "203.0.113.45" not in out
    assert "Failed password for invalid user <USER_1> from <IP_1> port 51234 ssh2" in out


def test_generic_accounts_are_kept_for_the_model():
    r = _r()
    out = r.redact(
        "sshd[1]: Failed password for root from 203.0.113.45 port 22 ssh2\n"
        "sshd[1]: Invalid user admin from 203.0.113.45 port 23"
    )
    assert "for root from <IP_1>" in out and "Invalid user admin from <IP_1>" in out


def test_sshd_accepted_publickey_and_pam_session():
    r = _r()
    a = r.redact("sshd[99]: Accepted publickey for julian from 192.168.1.20 port 50000 ssh2: ED25519 SHA256:x")
    b = r.redact("pam_unix(sshd:session): session opened for user julian(uid=1000) by (uid=0)")
    c = r.redact("sshd[99]: Disconnected from user julian 192.168.1.20 port 50000")
    assert "julian" not in a + b + c
    # Same user, same placeholder across lines of one request.
    assert "<USER_1>" in a and "<USER_1>" in b and "<USER_1>" in c
    assert c.count("<IP_1>") == 1


def test_sshd_invalid_user_and_closed_by_authenticating_user():
    r = _r()
    out = r.redact(
        "sshd[7]: Invalid user m.keller from 198.51.100.7 port 4000\n"
        "sshd[7]: Connection closed by authenticating user rbach 198.51.100.7 port 4001 [preauth]"
    )
    assert "m.keller" not in out and "rbach" not in out
    assert "Invalid user <USER_1> from <IP_1>" in out
    assert "authenticating user <USER_2> <IP_1> port 4001" in out


def test_sudo_line_keeps_structure():
    r = _r()
    out = r.redact("sudo:   julian : TTY=pts/0 ; PWD=/home/julian ; USER=root ; COMMAND=/usr/bin/apt update")
    assert "julian" not in out
    assert out.startswith("sudo:   <USER_1> : TTY=pts/0")
    assert "PWD=/home/<USER_1> ;" in out  # working directory, not treated as a password
    assert "USER=root ;" in out
    assert "COMMAND=/usr/bin/apt update" in out


# ── pfSense filterlog ────────────────────────────────────────────────────────

def test_pfsense_filterlog_csv():
    r = _r()
    line = (
        "filterlog[52100]: 5,,,1000000103,igb0,match,block,in,4,0x0,,64,12345,0,DF,6,tcp,60,"
        "203.0.113.45,10.10.30.5,51234,22,0,S,1234567,,64240,,mss;sackOK;TS;nop;wscale"
    )
    out = r.redact(line)
    assert "203.0.113.45" not in out and "10.10.30.5" not in out
    assert ",<IP_1>,<IP_2>,51234,22," in out
    assert out.startswith("filterlog[52100]: 5,,,1000000103,igb0,match,block,in,4,")


def test_ipv6_and_mac():
    r = _r()
    out = r.redact(
        "dhcpd: DHCPACK on 10.0.0.23 to 00:1a:2b:3c:4d:5e (laptop) via igb1; "
        "neighbor 2001:db8:85a3::8a2e:370:7334: reachable, fe80::1%igb0 gw; cisco 001a.2b3c.4d5e"
    )
    for v in ("10.0.0.23", "00:1a:2b:3c:4d:5e", "2001:db8:85a3::8a2e:370:7334", "fe80::1", "001a.2b3c.4d5e"):
        assert v not in out, v
    assert "<IPV6_1>: reachable" in out
    assert "<MAC_1>" in out and "<MAC_2>" in out


def test_timestamps_versions_and_loopback_survive():
    r = _r()
    out = r.redact("13:55:36 listening on 0.0.0.0:22 and 127.0.0.1 and ::1, took 12:30 min")
    assert out == "13:55:36 listening on 0.0.0.0:22 and 127.0.0.1 and ::1, took 12:30 min"


# ── Windows events ───────────────────────────────────────────────────────────

def test_windows_4625_logon_failure():
    r = _r()
    msg = (
        "An account failed to log on.\r\n"
        "Subject:\r\n\tSecurity ID:\t\tS-1-5-18\r\n\tAccount Name:\t\tDC01$\r\n"
        "Account For Which Logon Failed:\r\n"
        "\tSecurity ID:\t\tS-1-5-21-1004336348-1177238915-682003330-1105\r\n"
        "\tAccount Name:\t\tjdoe\r\n\tAccount Domain:\t\tCORP\r\n"
        "Network Information:\r\n\tWorkstation Name:\tJULIAN-PC\r\n"
        "\tSource Network Address:\t10.20.0.15\r\n\tSource Port:\t\t49833\r\n"
    )
    out = r.redact(msg)
    for v in ("jdoe", "JULIAN-PC", "10.20.0.15", "S-1-5-21-1004336348-1177238915-682003330-1105"):
        assert v not in out, v
    assert "S-1-5-18" in out  # well-known SYSTEM SID is not personal
    assert "Source Port:\t\t49833" in out


def test_windows_domain_backslash_user():
    r = _r()
    out = r.redact(r"Logon failure for CORP\jdoe on HKLM\Software\Policies; NT AUTHORITY\SYSTEM ok")
    assert r"CORP\jdoe" not in out
    assert r"HKLM\Software" in out
    assert r"AUTHORITY\SYSTEM" in out


# ── nginx access log ─────────────────────────────────────────────────────────

def test_nginx_combined_log_with_remote_user_and_token():
    r = _r()
    line = (
        '198.51.100.23 - julian [10/Oct/2026:13:55:36 +0200] "GET /api/export?token=abc123def456&page=2 HTTP/1.1" '
        '200 512 "https://grafana.example.ch/d/x" "Mozilla/5.0"'
    )
    out = r.redact(line)
    assert "198.51.100.23" not in out and "julian" not in out and "abc123def456" not in out
    assert out.startswith("<IP_1> - <USER_1> [10/Oct/2026:13:55:36 +0200]")
    assert "token=<SECRET_1>&page=2" in out
    # Hostname redaction is off by default.
    assert "grafana.example.ch" in out


def test_nginx_anonymous_request_untouched_user_field():
    r = _r()
    out = r.redact('203.0.113.9 - - [10/Oct/2026:13:55:36 +0200] "GET / HTTP/1.1" 404 0 "-" "curl/8.5"')
    assert out.startswith('<IP_1> - - [10/Oct/2026')


# ── secrets ──────────────────────────────────────────────────────────────────

def test_secrets_are_replaced_and_never_restored():
    r = _r()
    text = (
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N "
        "password=hunter2 api_key: 'k-12345678' url=https://backup:s3cr3t@nas.lan/share "
        "key sk-ant-api03-abcdefghijklmnopqrstuv"
    )
    out = r.redact(text)
    for v in ("hunter2", "k-12345678", "s3cr3t", "sk-ant-api03-abcdefghijklmnopqrstuv", "eyJhbGci"):
        assert v not in out, v
    assert re.search(r"Authorization: Bearer <SECRET_\d+> ", out)
    assert re.search(r"password=<SECRET_\d+> ", out)
    assert re.search(r"https://<USER_1>:<SECRET_\d+>@nas\.lan/share", out)
    # The answer may quote a placeholder: secrets stay masked, others come back.
    restored = r.restore(out)
    assert "hunter2" not in restored and "s3cr3t" not in restored
    assert "backup" in restored
    assert all(not v.startswith("<SECRET") for v in r.mapping)


def test_email_addresses():
    r = _r()
    out = r.redact("postfix/smtp: to=<julian@example.ch>, relay=mail.example.ch, status=sent; cc ops@corp.example")
    assert "julian@example.ch" not in out and "ops@corp.example" not in out
    assert "<EMAIL_1>" in out and "<EMAIL_2>" in out


# ── hostnames (optional) ─────────────────────────────────────────────────────

def test_hostnames_only_when_enabled():
    line = "nas01 backup to pbs.lan.example.ch failed; see /etc/nginx/nginx.conf and sshd.service"
    off = _r().redact(line)
    assert "pbs.lan.example.ch" in off and "nas01" in off

    r = _r(redact_hostnames=True, known_hostnames=["nas01", "Proxmox Node 1"])
    on = r.redact(line)
    assert "nas01" not in on and "pbs.lan.example.ch" not in on
    assert "nginx.conf" in on and "sshd.service" in on


# ── consistency, restore, streaming ──────────────────────────────────────────

def test_placeholders_are_stable_within_one_request_and_fresh_per_request():
    r = _r()
    a = r.redact("203.0.113.45 failed")
    b = r.redact("blocked 203.0.113.45 and 203.0.113.46")
    assert a == "<IP_1> failed"
    assert b == "blocked <IP_1> and <IP_2>"
    assert _r().redact("203.0.113.46") == "<IP_1>"


def test_restore_maps_back_and_leaves_unknown_placeholders():
    r = _r()
    r.redact("Failed password for julian from 203.0.113.45")
    answer = "Block <IP_1>; <USER_1> is targeted. <IP_9> is unknown."
    assert r.restore(answer) == "Block 203.0.113.45; julian is targeted. <IP_9> is unknown."


def test_values_found_once_are_replaced_everywhere_in_the_request():
    r = _r()
    context, question, history = r.redact_many([
        "sshd: Accepted password for julian from 192.168.1.20 port 2222",
        "Is julian allowed to log in from 192.168.1.20?",
        "Earlier: julian.bak and julianne are different words",
    ])
    assert context == "sshd: Accepted password for <USER_1> from <IP_1> port 2222"
    assert question == "Is <USER_1> allowed to log in from <IP_1>?"
    assert history == "Earlier: julian.bak and julianne are different words"


def test_stream_restorer_handles_split_placeholders():
    r = _r()
    r.redact("Failed password for julian from 203.0.113.45")
    s = r.stream_restorer()
    chunks = ["Block <", "IP", "_1> now; user <USE", "R_1>", " and a < b, done"]
    out = "".join(s.feed(c) for c in chunks) + s.flush()
    assert out == "Block 203.0.113.45 now; user julian and a < b, done"


def test_disabled_redactor_is_a_no_op():
    r = _r(enabled=False)
    text = "Failed password for julian from 203.0.113.45 password=x"
    assert r.redact(text) == text
    assert r.count == 0


def test_output_has_only_known_placeholder_kinds():
    r = _r(redact_hostnames=True)
    out = r.redact("user=bob from 10.1.1.1 to host.example.com mac 00:11:22:33:44:55 bob@example.com")
    kinds = {m.group(1) for m in PLACEHOLDER_RE.finditer(out)}
    assert kinds == {"USER", "IP", "HOST", "MAC", "EMAIL"}
