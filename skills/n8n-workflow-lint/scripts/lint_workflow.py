#!/usr/bin/env python3
"""Lint exported n8n workflow JSON files.

Checks: default (unnamed) node names, missing error workflow, hard-coded
credentials and tokens, hard-coded / plain-HTTP / internal URLs, disabled
nodes, unused and unreachable nodes, connections to missing nodes, duplicate
names, long node chains, very large workflows, pinned data left in the export,
legacy execution order and very long Code nodes. Across all inputs it also
reports Webhook nodes that register the same HTTP method and path (n8n refuses
to activate the second workflow).

Input: one or more workflow files or directories. Accepts a single workflow
object, the array written by ``n8n export:workflow --all``, or an API response
with a ``data`` array. Secret values are never printed, only where they are.

Exit codes: 0 = no findings at or above --fail-on, 1 = findings, 2 = error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

SEVERITIES = ["info", "low", "medium", "high"]

SENSITIVE_NAME_RE = re.compile(
    r"^(authorization|proxy-authorization|x-api-key|api[-_ ]?key|apikey|access[-_]?token|auth[-_]?token|"
    r"token|secret|client[-_]?secret|password|passwd|x-auth-token|private[-_]?key)$",
    re.I,
)
SENSITIVE_KEY_RE = re.compile(r"(password|passwd|secret|apikey|api_key|accesstoken|authtoken|privatekey)$", re.I)
TOKEN_PATTERNS = [
    ("bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    ("OpenAI-style key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ("Stripe key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{10,}")),
    ("Stripe webhook secret", re.compile(r"\bwhsec_[A-Za-z0-9_]{8,}")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghs|ghu|github_pat)_[A-Za-z0-9_]{20,}")),
    ("Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("password in URL", re.compile(r"://[^/\s:@'\"]+:[^/\s@'\"]+@")),
]
CODE_SECRET_RE = re.compile(r"(secret|password|passwd|api_?key|token)\w*['\"]?\s*[:=]\s*['\"][^'\"\s]{8,}['\"]", re.I)
CODE_TYPES = {"n8n-nodes-base.code", "n8n-nodes-base.function", "n8n-nodes-base.functionItem"}
HTTP_TYPES = {
    "n8n-nodes-base.httpRequest",
    "@n8n/n8n-nodes-langchain.toolHttpRequest",
    "n8n-nodes-base.httpRequestTool",
}
STICKY = "n8n-nodes-base.stickyNote"
WEBHOOK = "n8n-nodes-base.webhook"
INTERNAL_HOST_RE = re.compile(
    r"^https?://(localhost|127\.|0\.0\.0\.0|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|\[?::1\]?|[^/:]+\.local\b|[^/:.]+[:/])",
    re.I,
)
DEFAULT_NAME_ALIASES = {
    "set": ["editfields", "set"],
    "noop": ["nooperationdonothing", "noop"],
    "splitinbatches": ["loopoveritems", "splitinbatches"],
    "manualtrigger": ["whenclickingtestworkflow", "whenclickingexecuteworkflow", "manualtrigger"],
    "executeworkflowtrigger": ["whenexecutedbyanotherworkflow", "executeworkflowtrigger"],
    "chattrigger": ["whenchatmessagereceived", "chattrigger"],
    "agent": ["aiagent", "agent"],
    "lmchatopenai": ["openaichatmodel"],
    "lmchatanthropic": ["anthropicchatmodel"],
    "memorybufferwindow": ["windowbuffermemory", "simplememory"],
    "respondtowebhook": ["respondtowebhook"],
    "executeworkflow": ["executeworkflow", "executesubworkflow"],
    "emailsend": ["sendemail", "emailsend"],
    "scheduletrigger": ["scheduletrigger"],
    "if": ["if"],
}


@dataclass
class Finding:
    check: str
    severity: str
    node: str
    title: str
    fix: str = ""


# --------------------------------------------------------------------------- loading


def load_workflows(paths: list[str]) -> list[tuple[str, dict]]:
    """Return (label, workflow) pairs from files, directories or '-' (stdin)."""
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


# --------------------------------------------------------------------------- helpers


def is_trigger(node: dict) -> bool:
    t = node.get("type", "")
    short = t.rsplit(".", 1)[-1].lower()
    return short.endswith("trigger") or short in {"webhook", "start", "formtrigger"}


def iter_strings(value, path: str = ""):
    """Yield (path, string) for every string inside a parameter structure."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from iter_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from iter_strings(v, f"{path}[{i}]")


def iter_name_value(value, path: str = ""):
    """Yield (path, name, value) for {"name": ..., "value": ...} pairs (headers, query, body)."""
    if isinstance(value, dict):
        if isinstance(value.get("name"), str) and "value" in value:
            yield path, value["name"], value["value"]
        for k, v in value.items():
            yield from iter_name_value(v, f"{path}.{k}" if path else str(k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from iter_name_value(v, f"{path}[{i}]")


def iter_keyed(value, path: str = ""):
    if isinstance(value, dict):
        for k, v in value.items():
            p = f"{path}.{k}" if path else str(k)
            yield p, k, v
            yield from iter_keyed(v, p)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from iter_keyed(v, f"{path}[{i}]")


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def is_default_name(node: dict) -> bool:
    name = re.sub(r"\d+$", "", node.get("name", "")).strip()
    short = node.get("type", "").rsplit(".", 1)[-1].lower()
    short = re.sub(r"^tool(?=[a-z])", "", short)
    short = re.sub(r"tool$", "", short) or short
    candidates = {short, re.sub(r"trigger$", "", short)}
    candidates.update(DEFAULT_NAME_ALIASES.get(short, []))
    return norm(name) in {c for c in candidates if c}


def edges(wf: dict, kinds: str = "main") -> list[tuple[str, str, int, str]]:
    """Return (src, dst, output_index, type) for every connection."""
    out = []
    for src, by_type in (wf.get("connections") or {}).items():
        if not isinstance(by_type, dict):
            continue
        for ctype, outputs in by_type.items():
            if kinds == "main" and ctype != "main":
                continue
            for idx, targets in enumerate(outputs or []):
                for t in targets or []:
                    if isinstance(t, dict) and "node" in t:
                        out.append((src, t["node"], idx, ctype))
    return out


def longest_chain(names: set[str], main_edges: list[tuple[str, str, int, str]]) -> int:
    adj: dict[str, list[str]] = {n: [] for n in names}
    for s, d, _, _ in main_edges:
        if s in adj and d in adj:
            adj[s].append(d)
    # Remove back edges (loops) with an iterative DFS, then longest path on the DAG.
    state: dict[str, int] = {}
    order: list[str] = []
    dag: dict[str, list[str]] = {n: [] for n in names}
    for start in sorted(names):
        if start in state:
            continue
        stack = [(start, iter(adj[start]))]
        state[start] = 1
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                state[node] = 2
                order.append(node)
                stack.pop()
                continue
            if state.get(nxt) == 1:
                continue  # back edge
            dag[node].append(nxt)
            if nxt not in state:
                state[nxt] = 1
                stack.append((nxt, iter(adj[nxt])))
    best: dict[str, int] = {}
    for n in order:  # reverse topological (children first)
        best[n] = 1 + max((best[c] for c in dag[n]), default=0)
    return max(best.values(), default=0)


# --------------------------------------------------------------------------- checks


def lint(wf: dict, max_chain: int = 20, max_nodes: int = 50) -> list[Finding]:
    findings: list[Finding] = []
    nodes = [n for n in wf.get("nodes") or [] if isinstance(n, dict)]
    real = [n for n in nodes if n.get("type") != STICKY]
    names = [n.get("name", "") for n in nodes]
    name_set = set(names)

    for dup in sorted({n for n in names if names.count(n) > 1}):
        findings.append(Finding("node.duplicate_name", "high", dup, "two nodes share this name", "rename one of them"))

    all_edges = edges(wf, "all")
    for s, d, _, _ in all_edges:
        for end in (s, d):
            if end not in name_set:
                findings.append(
                    Finding(
                        "connections.dangling",
                        "high",
                        end,
                        "connection refers to a node that does not exist (renamed or deleted in JSON?)",
                        "fix the node name in 'connections' or reconnect in the editor",
                    )
                )
    has_error_trigger = any(n.get("type") == "n8n-nodes-base.errorTrigger" for n in nodes)
    triggers = [n for n in real if is_trigger(n)]
    settings = wf.get("settings") or {}

    # error workflow
    if not settings.get("errorWorkflow") and not has_error_trigger:
        manual_only = triggers and all(n.get("type") == "n8n-nodes-base.manualTrigger" for n in triggers)
        findings.append(
            Finding(
                "settings.error_workflow",
                "info" if manual_only or not triggers else "medium",
                "-",
                "no error workflow set: failures in production are silent",
                "Workflow settings > Error workflow (see the n8n-error-handling skill)",
            )
        )
    if settings.get("executionOrder", "v1") != "v1":
        findings.append(
            Finding(
                "settings.execution_order",
                "info",
                "-",
                f"legacy execution order '{settings.get('executionOrder')}'",
                "switch to v1 in Workflow settings and re-test branches",
            )
        )
    if wf.get("pinData"):
        findings.append(
            Finding(
                "pindata.present",
                "low",
                ", ".join(sorted(wf["pinData"]))[:80],
                "pinned test data is stored in the export (may contain personal data)",
                "unpin data before exporting or committing the workflow",
            )
        )
    if len(real) > max_nodes:
        findings.append(
            Finding(
                "workflow.large",
                "low",
                "-",
                f"{len(real)} nodes (limit {max_nodes})",
                "split into sub-workflows with Execute Workflow",
            )
        )

    for n in real:
        name = n.get("name", "?")
        typ = n.get("type", "")
        params = n.get("parameters") or {}
        if is_default_name(n):
            findings.append(
                Finding(
                    "node.default_name",
                    "info" if is_trigger(n) else "low",
                    name,
                    "node still has its default name",
                    "rename to what it does, e.g. 'Fetch open invoices'",
                )
            )
        if n.get("disabled"):
            findings.append(Finding("node.disabled", "low", name, "node is disabled", "delete it or re-enable it"))
        findings += _secret_findings(name, typ, params)
        if typ in HTTP_TYPES:
            url = params.get("url", "")
            if isinstance(url, str) and url and not url.startswith("="):
                if INTERNAL_HOST_RE.match(url):
                    findings.append(
                        Finding(
                            "url.internal",
                            "medium",
                            name,
                            "hard-coded internal/localhost URL (breaks between environments)",
                            "put the base URL in a credential, Variables ($vars) or a config node",
                        )
                    )
                elif url.lower().startswith("http://"):
                    findings.append(
                        Finding(
                            "url.plain_http",
                            "medium",
                            name,
                            "plain HTTP URL",
                            "use https://",
                        )
                    )
                else:
                    findings.append(
                        Finding(
                            "url.hardcoded",
                            "info",
                            name,
                            "hard-coded URL",
                            "consider a credential base URL or $vars for per-environment hosts",
                        )
                    )
        if typ in CODE_TYPES:
            code = params.get("jsCode") or params.get("pythonCode") or params.get("functionCode") or ""
            if isinstance(code, str) and code.count("\n") + 1 > 150:
                findings.append(
                    Finding(
                        "code.long",
                        "info",
                        name,
                        f"Code node has {code.count(chr(10)) + 1} lines",
                        "move logic into smaller nodes or a sub-workflow",
                    )
                )

    # unused / unreachable
    main_edges = [e for e in all_edges if e[3] == "main"]
    connected = {s for s, _, _, _ in all_edges} | {d for _, d, _, _ in all_edges}
    for n in real:
        if n.get("name") not in connected and len(real) > 1:
            findings.append(
                Finding(
                    "node.unused",
                    "low",
                    n.get("name", "?"),
                    "node is not connected to anything",
                    "delete it or connect it",
                )
            )
    if triggers:
        reach = {t["name"] for t in triggers}
        frontier = list(reach)
        while frontier:
            cur = frontier.pop()
            for s, d, _, _ in main_edges:
                if s == cur and d not in reach:
                    reach.add(d)
                    frontier.append(d)
        changed = True
        while changed:  # AI sub-nodes (model, memory, tools) attach to a reachable root node
            changed = False
            for s, d, _, ctype in all_edges:
                if ctype != "main" and d in reach and s not in reach:
                    reach.add(s)
                    changed = True
        for n in real:
            nm = n.get("name")
            if nm in connected and nm not in reach:
                findings.append(
                    Finding(
                        "node.unreachable",
                        "low",
                        nm,
                        "no trigger leads to this node",
                        "connect it to the flow or delete it",
                    )
                )
    chain = longest_chain({n.get("name") for n in real}, main_edges)
    if chain > max_chain:
        findings.append(
            Finding(
                "chain.long",
                "low",
                "-",
                f"longest chain is {chain} nodes (limit {max_chain})",
                "extract steps into sub-workflows; easier to test and reuse",
            )
        )
    order = {s: i for i, s in enumerate(reversed(SEVERITIES))}
    findings.sort(key=lambda f: (order[f.severity], f.check, f.node))
    return findings


def _secret_findings(name: str, typ: str, params: dict) -> list[Finding]:
    out: list[Finding] = []
    seen: set[str] = set()

    def add(path: str, what: str) -> None:
        if any(path.startswith(p) or p.startswith(path) for p in seen):
            return
        seen.add(path)
        out.append(
            Finding(
                "credentials.hardcoded",
                "high",
                name,
                f"{what} hard-coded in parameter '{path}' (value hidden)",
                "move it to an n8n credential (Header Auth, Query Auth, predefined type) and rotate it",
            )
        )

    for path, key, value in iter_name_value(params):
        if SENSITIVE_NAME_RE.match(key.strip()) and isinstance(value, str):
            v = value.strip()
            if (
                v
                and not v.startswith("=")
                or v.startswith("=")
                and re.search(r"['\"][^'\"]{12,}['\"]", v)
                and "$" not in v
            ):
                add(path, f"'{key}'")
    for path, key, value in iter_keyed(params):
        if isinstance(value, str) and SENSITIVE_KEY_RE.search(key) and value and not value.startswith("="):
            add(path, f"'{key}'")
    for path, s in iter_strings(params):
        for label, rx in TOKEN_PATTERNS:
            if rx.search(s):
                add(path, label)
                break
        else:
            if typ in CODE_TYPES and CODE_SECRET_RE.search(s):
                add(path, "secret-looking literal in code")
    return out


def webhook_routes(node: dict) -> list[tuple[str, str]]:
    """Return the (METHOD, path) pairs a Webhook node registers; [] if none apply."""
    if node.get("type") != WEBHOOK or node.get("disabled"):
        return []
    params = node.get("parameters") or {}
    path = params.get("path", node.get("webhookId", ""))
    if not isinstance(path, str) or not path.strip("/") or path.startswith("="):
        return []
    if params.get("multipleMethods"):
        methods = params.get("httpMethod") or ["GET", "POST"]
        if not isinstance(methods, list):
            methods = [methods]
    else:
        methods = [params.get("httpMethod") or "GET"]
    path = re.sub(r"/{2,}", "/", path.strip().strip("/"))
    return [(str(m).upper(), path) for m in methods if isinstance(m, str)]


def webhook_conflicts(workflows: list[tuple[str, dict]]) -> dict[str, list[Finding]]:
    """Find Webhook nodes (in one or several workflows) that share an HTTP method and path.

    n8n registers production webhooks by method + path, so a second active workflow with
    the same route fails to activate. Returns findings keyed by workflow label.
    """
    routes: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    for label, wf in workflows:
        for node in wf.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            for route in webhook_routes(node):
                routes.setdefault(route, []).append((label, wf.get("name", ""), node.get("name", "?")))
    out: dict[str, list[Finding]] = {}
    for (method, path), users in sorted(routes.items()):
        if len(users) < 2:
            continue
        for label, _, node_name in users:
            others = [f"'{n}' in {w or lb}" for lb, w, n in users if (lb, n) != (label, node_name)]
            out.setdefault(label, []).append(
                Finding(
                    "webhook.path_conflict",
                    "high",
                    node_name,
                    f"{method} /{path} is also registered by " + ", ".join(others),
                    "give each Webhook node a unique path (or method); only one active workflow can own a route",
                )
            )
    return out


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Lint exported n8n workflow JSON (files, directories or - for stdin).",
        epilog="Exit codes: 0 = clean, 1 = findings at or above --fail-on, 2 = error.",
    )
    p.add_argument("paths", nargs="+", help="workflow .json files or directories")
    p.add_argument("--json", action="store_true", help="print JSON")
    p.add_argument("--fail-on", default="medium", choices=[*SEVERITIES, "none"])
    p.add_argument("--min-severity", default="info", choices=SEVERITIES, help="hide findings below this")
    p.add_argument("--max-chain", type=int, default=20, help="longest allowed node chain (default 20)")
    p.add_argument("--max-nodes", type=int, default=50, help="largest allowed workflow (default 50)")
    p.add_argument("--ignore", action="append", default=[], help="check id or prefix to skip (repeatable)")
    p.add_argument(
        "--no-cross-workflow",
        action="store_true",
        help="skip checks that compare workflows with each other (webhook.path_conflict)",
    )
    args = p.parse_args(argv)
    try:
        workflows = load_workflows(args.paths)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    min_rank = SEVERITIES.index(args.min_severity)
    report = []
    worst = -1
    conflicts = {} if args.no_cross_workflow else webhook_conflicts(workflows)
    order = {s: i for i, s in enumerate(reversed(SEVERITIES))}
    for label, wf in workflows:
        found = lint(wf, args.max_chain, args.max_nodes) + conflicts.get(label, [])
        found.sort(key=lambda f: (order[f.severity], f.check, f.node))
        fs = [
            f
            for f in found
            if SEVERITIES.index(f.severity) >= min_rank and not any(f.check.startswith(i) for i in args.ignore)
        ]
        worst = max([worst, *(SEVERITIES.index(f.severity) for f in fs)])
        report.append({"file": label, "workflow": wf.get("name", ""), "findings": [asdict(f) for f in fs]})
    counts = {s: sum(1 for r in report for f in r["findings"] if f["severity"] == s) for s in SEVERITIES}
    if args.json:
        print(json.dumps({"files": report, "summary": counts}, indent=2))
    else:
        for r in report:
            print(f"== {r['file']} ({r['workflow']}): {len(r['findings'])} finding(s)")
            for f in r["findings"]:
                print(f"  {f['severity'].upper():6} {f['check']:24} [{f['node']}] {f['title']}")
                if f["fix"]:
                    print(f"         fix: {f['fix']}")
        print(f"\n{len(report)} workflow(s): " + ", ".join(f"{counts[s]} {s}" for s in reversed(SEVERITIES)))
    if args.fail_on == "none":
        return 0
    return 1 if worst >= SEVERITIES.index(args.fail_on) else 0


if __name__ == "__main__":
    sys.exit(main())
