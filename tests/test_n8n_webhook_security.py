import base64
import hashlib
import hmac
import json
import os
import time

import pytest

from conftest import WORKFLOWS, chain, load_script, load_wf, make_wf, run_script

SKILL = "n8n-webhook-security"
AUDIT = "webhook_audit.py"
SIGN = "sign_webhook.py"
audit_mod = load_script(SKILL, AUDIT)
sign_mod = load_script(SKILL, SIGN)
SECRET = "unit-test-secret-not-real"


def checks(wf):
    return {(f.check, f.node) for f in audit_mod.audit(wf)[1]}


def test_unauthenticated_webhook():
    got = checks(load_wf("order-sync-bad.json"))
    assert ("webhook.no_auth", "Webhook") in got
    assert ("webhook.guessable_path", "Webhook") in got
    assert ("webhook.response_leaks", "Webhook") in got
    assert ("webhook.rate_limit", "-") in got


def test_weak_signature_check():
    got = checks(load_wf("stripe-webhook-weak.json"))
    assert ("webhook.no_auth", "Stripe events") not in got  # a verifier exists
    assert ("webhook.verify_without_raw_body", "Stripe events") in got
    assert ("webhook.signature_compare", "Check signature") in got
    assert ("webhook.no_replay_protection", "Check signature") in got
    assert ("webhook.secret_in_workflow", "Check signature") in got
    assert ("webhook.cors_any", "Stripe events") in got
    # the disabled Crypto node is ignored
    assert not any(n == "Hmac check" for _, n in got)


def test_good_webhook_only_info():
    eps, findings = audit_mod.audit(load_wf("order-sync-good.json"))
    assert eps[0]["auth"] == "none" and eps[0]["respond"] == "responseNode"
    assert {f.check for f in findings} == {"webhook.rate_limit"}


def test_public_chat():
    got = checks(load_wf("ai-support-agent-bad.json"))
    assert ("webhook.public_chat", "When chat message received") in got


def test_header_auth_webhook_is_fine():
    wf = make_wf(
        [
            {
                "name": "From ERP",
                "type": "n8n-nodes-base.webhook",
                "parameters": {"path": "erp", "authentication": "headerAuth", "httpMethod": "POST"},
            }
        ]
    )
    assert checks(wf) == set()


def test_crypto_node_secret_literal_and_form():
    nodes = [
        {
            "name": "Hook",
            "type": "n8n-nodes-base.webhook",
            "parameters": {"path": "x", "options": {"rawBody": True}},
        },
        {
            "name": "HMAC",
            "type": "n8n-nodes-base.crypto",
            "parameters": {"action": "hmac", "secret": "literal-secret"},
        },
        {"name": "Signup form", "type": "n8n-nodes-base.formTrigger", "parameters": {"formTitle": "Join"}},
    ]
    got = checks(make_wf(nodes, chain("Hook", "HMAC")))
    assert ("webhook.secret_in_workflow", "HMAC") in got
    assert ("webhook.public_form", "Signup form") in got
    assert ("webhook.no_auth", "Hook") not in got


def test_jwt_verify_counts_as_verifier():
    nodes = [
        {"name": "Hook", "type": "n8n-nodes-base.webhook", "parameters": {"path": "x"}},
        {"name": "Verify JWT", "type": "n8n-nodes-base.jwt", "parameters": {"operation": "verify"}},
    ]
    got = checks(make_wf(nodes, chain("Hook", "Verify JWT")))
    assert ("webhook.no_auth", "Hook") not in got
    assert ("webhook.verify_without_raw_body", "Hook") not in got


def test_audit_cli():
    r = run_script(SKILL, AUDIT, str(WORKFLOWS), "--json")
    assert r.returncode == 1
    data = json.loads(r.stdout)
    assert {f["workflow"] for f in data["files"]} >= {"Order sync (legacy)", "Stripe: invoice paid"}
    assert "whsec_FAKE" not in r.stdout
    assert run_script(SKILL, AUDIT, str(WORKFLOWS / "order-sync-good.json")).returncode == 0
    r = run_script(SKILL, AUDIT, str(WORKFLOWS / "ai-agent-good.json"))
    assert r.returncode == 0 and "no Webhook" in r.stdout
    assert run_script(SKILL, AUDIT, "missing.json").returncode == 2
    assert run_script(SKILL, AUDIT, "--help").returncode == 0


# ------------------------------------------------------------------ signer


def ref_hex(msg: bytes) -> str:
    return hmac.new(SECRET.encode(), msg, hashlib.sha256).hexdigest()


@pytest.mark.parametrize(
    "scheme,expected",
    [
        ("github", {"X-Hub-Signature-256": "sha256=" + ref_hex(b'{"a":1}')}),
        ("stripe", {"Stripe-Signature": "t=1700000000,v1=" + ref_hex(b'1700000000.{"a":1}')}),
        (
            "shopify",
            {
                "X-Shopify-Hmac-Sha256": base64.b64encode(
                    hmac.new(SECRET.encode(), b'{"a":1}', hashlib.sha256).digest()
                ).decode()
            },
        ),
        (
            "slack",
            {
                "X-Slack-Request-Timestamp": "1700000000",
                "X-Slack-Signature": "v0=" + ref_hex(b'v0:1700000000:{"a":1}'),
            },
        ),
        (
            "generic",
            {"X-Timestamp": "1700000000", "X-Signature": "sha256=" + ref_hex(b'1700000000.{"a":1}')},
        ),
    ],
)
def test_sign_matches_reference(scheme, expected):
    assert sign_mod.sign(scheme, SECRET.encode(), b'{"a":1}', 1700000000) == expected


def test_verify_roundtrip_tamper_and_replay():
    body = b'{"id":"evt_1"}'
    now = int(time.time())
    for scheme in ("github", "shopify"):
        sig = next(iter(sign_mod.sign(scheme, SECRET.encode(), body, now).values()))
        assert sign_mod.verify(scheme, SECRET.encode(), body, sig, None, 300)[0]
        assert not sign_mod.verify(scheme, SECRET.encode(), body + b" ", sig, None, 300)[0]
    stripe = sign_mod.sign("stripe", SECRET.encode(), body, now)["Stripe-Signature"]
    assert sign_mod.verify("stripe", SECRET.encode(), body, stripe, None, 300) == (True, "valid")
    old = sign_mod.sign("stripe", SECRET.encode(), body, now - 3600)["Stripe-Signature"]
    ok, reason = sign_mod.verify("stripe", SECRET.encode(), body, old, None, 300)
    assert not ok and "replay" in reason
    assert sign_mod.verify("stripe", SECRET.encode(), body, old, None, 0)[0]
    assert not sign_mod.verify("stripe", SECRET.encode(), body, "v1=abc", None, 300)[0]
    slack = sign_mod.sign("slack", SECRET.encode(), body, now)["X-Slack-Signature"]
    assert sign_mod.verify("slack", SECRET.encode(), body, slack, now, 300)[0]
    assert not sign_mod.verify("slack", SECRET.encode(), body, slack, None, 300)[0]


def test_sign_cli_never_prints_secret(tmp_path):
    body = tmp_path / "payload.json"
    body.write_bytes(b'{"id":1}')
    env = {**os.environ, "WEBHOOK_SECRET": SECRET}
    r = run_script(SKILL, SIGN, "--scheme", "github", "--body", str(body), env=env)
    assert r.returncode == 0 and r.stdout.startswith("X-Hub-Signature-256: sha256=")
    assert SECRET not in r.stdout + r.stderr
    sig = r.stdout.split(": ", 1)[1].strip()
    ok = run_script(SKILL, SIGN, "--scheme", "github", "--body", str(body), "--verify", sig, "--json", env=env)
    assert ok.returncode == 0 and json.loads(ok.stdout)["valid"] is True
    bad = run_script(SKILL, SIGN, "--scheme", "github", "--body", str(body), "--verify", "sha256=00", env=env)
    assert bad.returncode == 1 and "INVALID" in bad.stdout
    curl = run_script(
        SKILL,
        SIGN,
        "--scheme",
        "stripe",
        "--body",
        str(body),
        "--curl",
        "https://n8n.example.com/webhook/x",
        env=env,
    )
    assert curl.returncode == 0 and curl.stdout.startswith("curl ") and "Stripe-Signature: t=" in curl.stdout
    j = run_script(
        SKILL,
        SIGN,
        "--scheme",
        "slack",
        "--body",
        "-",
        "--timestamp",
        "1700000000",
        "--json",
        stdin='{"id":1}',
        env=env,
    )
    assert json.loads(j.stdout)["headers"]["X-Slack-Request-Timestamp"] == "1700000000"


def test_sign_cli_errors(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "WEBHOOK_SECRET"}
    body = tmp_path / "b.json"
    body.write_text("{}")
    assert run_script(SKILL, SIGN, "--scheme", "github", "--body", str(body), env=env).returncode == 2
    env["WEBHOOK_SECRET"] = SECRET
    assert run_script(SKILL, SIGN, "--scheme", "github", "--body", str(tmp_path / "none"), env=env).returncode == 2
    assert (
        run_script(SKILL, SIGN, "--scheme", "github", "--body", "-", "--curl", "https://x.example", env=env).returncode
        == 2
    )
    assert run_script(SKILL, SIGN, "--help").returncode == 0
