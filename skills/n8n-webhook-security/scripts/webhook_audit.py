#!/usr/bin/env python3
"""Audit the public entry points of exported n8n workflows.

Finds Webhook, Form Trigger and Chat Trigger nodes and reports:

  webhook.no_auth                 no authentication and no signature check after it
  webhook.public_chat             public Chat Trigger without authentication (pays for LLM calls)
  webhook.public_form             public Form Trigger (fine for real forms; add bot protection)
  webhook.verify_without_raw_body signature checked but Raw Body is off (re-serialised JSON)
  webhook.signature_compare       signature compared with ===/includes instead of timingSafeEqual
  webhook.no_replay_protection    signature checked but no timestamp window
  webhook.secret_in_workflow      HMAC secret written into the Code/Crypto node
  webhook.guessable_path          short, guessable path on an unauthenticated webhook
  webhook.response_leaks          responds with the last node's data to an unauthenticated caller
  webhook.cors_any                Allowed Origins is '*'
  webhook.rate_limit              public endpoint: n8n has no built-in rate limit

Secret values are never printed. Exit codes: 0 = no findings at or above
--fail-on, 1 = findings, 2 = error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

SEVERITIES = ["info", "low", "medium", "high"]
WEBHOOK = "n8n-nodes-base.webhook"
FORM = "n8n-nodes-base.formTrigger"
CHAT = "@n8n/n8n-nodes-langchain.chatTrigger"
CODE = {"n8n-nodes-base.code", "n8n-nodes-base.function", "n8n-nodes-base.functionItem"}
UUIDISH_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[A-Za-z0-9_-]{24,}", re.I)
VERIFY_CODE_RE = re.compile(r"createHmac|crypto\.subtle|jwt\.verify|jose\.|verifySignature", re.I)
TIMING_SAFE_RE = re.compile(r"timingSafeEqual")
REPLAY_RE = re.compile(r"timestamp|Date\.now\(\)|\$now|tolerance|max_?age", re.I)
LITERAL_SECRET_RE = re.compile(r"createHmac\(\s*['\"][^'\"]+['\"]\s*,\s*['\"][^'\"]{6,}['\"]")


@dataclass
class Finding:
    check: str
    severity: str
    node: str
    title: str
    fix: str = ""


def load_workflows(paths: list[str]) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for p in paths:
        if p == "-":
            out += _extract("<stdin>", json.loads(sys.stdin.read()))
            continue
        path = Path(p)
        files = sorted(path.rglob("*.json")) if path.is_dir() else [path]
        for f in files:
            out += _extract(str(f), json.loads(f.read_text(encoding="utf-8")))
    return out


def _extract(label: str, data) -> list[tuple[str, dict]]:
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        data = data["data"]
    if isinstance(data, list):
        wfs = [w for w in data if isinstance(w, dict) and "nodes" in w]
        return [(f"{label}#{i}" if len(wfs) > 1 else label, w) for i, w in enumerate(wfs)]
    if isinstance(data, dict) and "nodes" in data:
        return [(label, data)]
    raise ValueError(f"{label}: not an n8n workflow export (no 'nodes')")


def downstream(wf: dict, start: str, limit: int = 6) -> list[str]:
    """Nodes reachable from start within `limit` hops (breadth first, in order)."""
    conns = wf.get("connections") or {}
    seen = [start]
    frontier = [start]
    for _ in range(limit):
        nxt = []
        for cur in frontier:
            for targets in (conns.get(cur) or {}).get("main") or []:
                for t in targets or []:
                    if t.get("node") not in seen:
                        seen.append(t["node"])
                        nxt.append(t["node"])
        frontier = nxt
    return seen[1:]


def verifier_nodes(wf: dict, start: str) -> list[dict]:
    by_name = {n.get("name"): n for n in wf.get("nodes") or []}
    out = []
    for name in downstream(wf, start):
        n = by_name.get(name) or {}
        if n.get("disabled"):
            continue
        typ = n.get("type", "")
        params = n.get("parameters") or {}
        code = params.get("jsCode") or params.get("functionCode") or ""
        if (
            (typ in CODE and VERIFY_CODE_RE.search(str(code)))
            or (typ == "n8n-nodes-base.crypto" and params.get("action") == "hmac")
            or typ == "n8n-nodes-base.jwt"
            and params.get("operation") == "verify"
        ):
            out.append(n)
    return out


def endpoint_summary(node: dict) -> dict:
    params = node.get("parameters") or {}
    typ = node.get("type")
    if typ == CHAT:
        auth = params.get("authentication", "none") if params.get("public") else "n8n editor only"
        return {
            "node": node["name"],
            "kind": "chat",
            "method": "POST",
            "path": node.get("webhookId", ""),
            "auth": auth,
        }
    return {
        "node": node.get("name"),
        "kind": "form" if typ == FORM else "webhook",
        "method": params.get("httpMethod", "GET" if typ == FORM else "GET"),
        "path": params.get("path", node.get("webhookId", "")),
        "auth": params.get("authentication", "none"),
        "respond": params.get("responseMode", "onReceived"),
    }


def audit(wf: dict) -> tuple[list[dict], list[Finding]]:
    findings: list[Finding] = []
    endpoints = []
    public = False
    for n in wf.get("nodes") or []:
        typ = n.get("type")
        if typ not in (WEBHOOK, FORM, CHAT) or n.get("disabled"):
            continue
        name = n.get("name", "?")
        params = n.get("parameters") or {}
        options = params.get("options") or {}
        endpoints.append(endpoint_summary(n))
        auth = params.get("authentication", "none") or "none"
        if typ == CHAT:
            if params.get("public") and auth == "none":
                public = True
                findings.append(
                    Finding(
                        "webhook.public_chat",
                        "medium",
                        name,
                        "public chat without authentication: anyone can run the agent on your LLM bill",
                        "set Authentication (Basic Auth or n8n user auth) or front it with your own app",
                    )
                )
            continue
        if typ == FORM:
            if auth == "none":
                public = True
                findings.append(
                    Finding(
                        "webhook.public_form",
                        "info",
                        name,
                        "public form",
                        "fine for real forms; add a honeypot field and proxy rate limits",
                    )
                )
            continue

        verifiers = verifier_nodes(wf, name)
        path = str(params.get("path", ""))
        if auth == "none" and not verifiers:
            public = True
            findings.append(
                Finding(
                    "webhook.no_auth",
                    "high",
                    name,
                    "no authentication and no signature verification: anyone who learns the URL can trigger it",
                    "set Authentication (Header Auth / Basic / JWT), verify the sender's HMAC signature, "
                    "or restrict IP(s) Allowlist",
                )
            )
            if not UUIDISH_RE.search(path) and len(path) < 24:
                findings.append(
                    Finding(
                        "webhook.guessable_path",
                        "low",
                        name,
                        f"short, guessable path '{path}'",
                        "use a random path (the default UUID) - but never treat the path as the only secret",
                    )
                )
            if params.get("responseMode") == "lastNode":
                findings.append(
                    Finding(
                        "webhook.response_leaks",
                        "low",
                        name,
                        "returns the last node's output to an unauthenticated caller",
                        "respond 'Immediately' or use Respond to Webhook with only the fields the caller needs",
                    )
                )
        elif auth == "none":
            public = True  # authenticated by signature only
        for v in verifiers:
            vparams = v.get("parameters") or {}
            code = str(vparams.get("jsCode") or vparams.get("functionCode") or "")
            if v.get("type") != "n8n-nodes-base.jwt" and not options.get("rawBody"):
                findings.append(
                    Finding(
                        "webhook.verify_without_raw_body",
                        "medium",
                        name,
                        f"'{v['name']}' checks a signature but Raw Body is off: n8n re-serialises the JSON, "
                        "so valid requests fail or the check is done over the wrong bytes",
                        "enable Options > Raw Body on the webhook and HMAC the raw bytes",
                    )
                )
            if v.get("type") in CODE:
                if not TIMING_SAFE_RE.search(code):
                    findings.append(
                        Finding(
                            "webhook.signature_compare",
                            "low",
                            v["name"],
                            "signature compared without crypto.timingSafeEqual",
                            "compare equal-length buffers with crypto.timingSafeEqual",
                        )
                    )
                if not REPLAY_RE.search(code):
                    findings.append(
                        Finding(
                            "webhook.no_replay_protection",
                            "low",
                            v["name"],
                            "no timestamp check: a captured request can be replayed",
                            "reject requests older than 5 minutes (Stripe t=, Slack X-Slack-Request-Timestamp)",
                        )
                    )
                if LITERAL_SECRET_RE.search(code):
                    findings.append(
                        Finding(
                            "webhook.secret_in_workflow",
                            "high",
                            v["name"],
                            "HMAC secret is a literal in the code (value hidden)",
                            "load it from a credential-backed node or sub-workflow; rotate the secret",
                        )
                    )
            if v.get("type") == "n8n-nodes-base.crypto":
                secret = vparams.get("secret", "")
                if isinstance(secret, str) and secret and not secret.startswith("="):
                    findings.append(
                        Finding(
                            "webhook.secret_in_workflow",
                            "high",
                            v["name"],
                            "HMAC secret typed into the Crypto node (value hidden)",
                            "use a credential if your n8n version offers one, otherwise load it at runtime; rotate it",
                        )
                    )
        if options.get("allowedOrigins") == "*":
            findings.append(
                Finding(
                    "webhook.cors_any",
                    "info",
                    name,
                    "Allowed Origins (CORS) is '*'",
                    "list the browser origins that call it, or leave CORS unused for server-to-server hooks",
                )
            )
    if public:
        findings.append(
            Finding(
                "webhook.rate_limit",
                "info",
                "-",
                "public endpoint: n8n has no built-in rate limiting",
                "rate-limit /webhook/ at the reverse proxy (see references/rate-limiting.md)",
            )
        )
    order = {s: i for i, s in enumerate(reversed(SEVERITIES))}
    findings.sort(key=lambda f: (order[f.severity], f.check, f.node))
    return endpoints, findings


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Audit Webhook, Form and Chat triggers in exported n8n workflows.",
        epilog="Exit codes: 0 = clean, 1 = findings at or above --fail-on, 2 = error.",
    )
    p.add_argument("paths", nargs="+", help="workflow .json files or directories, or - for stdin")
    p.add_argument("--json", action="store_true", help="print JSON")
    p.add_argument("--fail-on", default="medium", choices=[*SEVERITIES, "none"])
    args = p.parse_args(argv)
    try:
        workflows = load_workflows(args.paths)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    report = []
    worst = -1
    for label, wf in workflows:
        eps, fs = audit(wf)
        if not eps:
            continue
        worst = max([worst, *(SEVERITIES.index(f.severity) for f in fs)])
        report.append(
            {
                "file": label,
                "workflow": wf.get("name", ""),
                "endpoints": eps,
                "findings": [asdict(f) for f in fs],
            }
        )
    if args.json:
        print(json.dumps({"files": report}, indent=2))
    else:
        if not report:
            print("no Webhook, Form or Chat trigger found")
        for r in report:
            print(f"== {r['file']} ({r['workflow']})")
            for e in r["endpoints"]:
                print(f"  ENDPOINT {e['kind']:7} {e['method']:6} /{e['path']}  auth={e['auth']}  [{e['node']}]")
            for f in r["findings"]:
                print(f"  {f['severity'].upper():6} {f['check']:32} [{f['node']}] {f['title']}")
                if f["fix"]:
                    print(f"         fix: {f['fix']}")
    if args.fail_on == "none":
        return 0
    return 1 if worst >= SEVERITIES.index(args.fail_on) else 0


if __name__ == "__main__":
    sys.exit(main())
