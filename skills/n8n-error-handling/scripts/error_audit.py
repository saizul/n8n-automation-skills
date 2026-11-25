#!/usr/bin/env python3
"""Audit error handling across a set of exported n8n workflows.

Pass every workflow you have (a directory from ``n8n export:workflow --all
--separate``, the single file from ``--all`` or individual files) so the audit
can follow ``settings.errorWorkflow`` references.

Checks:
  error.no_error_workflow        production trigger but no error workflow set
  error.unknown_error_workflow   errorWorkflow id is not among the files given
  error.target_not_error_trigger errorWorkflow points at a workflow without an Error Trigger
  error.self_reference           a workflow is its own error workflow
  error.handler_no_notify        Error Trigger workflow that notifies nobody
  error.swallowed                continueOnFail / onError=continueRegularOutput hides failures
  error.error_output_unconnected onError=continueErrorOutput but the error output goes nowhere
  error.no_retry                 node calls an external service without Retry On Fail
  error.retry_not_idempotent     POST/PATCH retried without an Idempotency-Key header
  error.failed_not_saved         failed executions are not saved (saveDataErrorExecution=none)

Exit codes: 0 = no findings at or above --fail-on, 1 = findings, 2 = error.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

SEVERITIES = ["info", "low", "medium", "high"]
ERROR_TRIGGER = "n8n-nodes-base.errorTrigger"
STICKY = "n8n-nodes-base.stickyNote"
NOTIFY_HINTS = (
    "slack",
    "email",
    "gmail",
    "telegram",
    "discord",
    "microsoftteams",
    "mattermost",
    "pagerduty",
    "opsgenie",
    "twilio",
    "httprequest",
    "sendgrid",
    "mailgun",
    "pushover",
    "matrix",
    "rocketchat",
    "googlechat",
    "jira",
    "linear",
    "github",
)
MULTI_OUTPUT = {"n8n-nodes-base.if": 2, "n8n-nodes-base.filter": 1, "n8n-nodes-base.splitInBatches": 2}
NO_RETRY_NEEDED_PREFIX = (
    "@n8n/n8n-nodes-langchain.lm",
    "@n8n/n8n-nodes-langchain.memory",
    "@n8n/n8n-nodes-langchain.embeddings",
)


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


def is_trigger(node: dict) -> bool:
    short = node.get("type", "").rsplit(".", 1)[-1].lower()
    return short.endswith("trigger") or short in {"webhook", "start"}


def outputs_of(wf: dict, name: str) -> list[list[dict]]:
    return ((wf.get("connections") or {}).get(name) or {}).get("main") or []


def downstream(wf: dict, start: str) -> set[str]:
    seen = {start}
    stack = [start]
    while stack:
        cur = stack.pop()
        for targets in outputs_of(wf, cur):
            for t in targets or []:
                if t.get("node") not in seen:
                    seen.add(t["node"])
                    stack.append(t["node"])
    return seen


def has_header(params: dict, header: str) -> bool:
    for p in (params.get("headerParameters") or {}).get("parameters") or []:
        if isinstance(p, dict) and str(p.get("name", "")).lower() == header:
            return True
    return False


def audit(wf: dict, by_id: dict[str, dict]) -> list[Finding]:
    findings: list[Finding] = []
    nodes = [n for n in wf.get("nodes") or [] if isinstance(n, dict) and n.get("type") != STICKY]
    by_name = {n.get("name"): n for n in nodes}
    settings = wf.get("settings") or {}
    triggers = [n for n in nodes if is_trigger(n)]
    error_triggers = [n for n in nodes if n.get("type") == ERROR_TRIGGER]
    production = [n for n in triggers if n.get("type") not in (ERROR_TRIGGER, "n8n-nodes-base.manualTrigger")]
    target = settings.get("errorWorkflow")

    if error_triggers:
        for et in error_triggers:
            reach = downstream(wf, et["name"]) - {et["name"]}
            notifiers = [r for r in reach if any(h in by_name.get(r, {}).get("type", "").lower() for h in NOTIFY_HINTS)]
            if not notifiers:
                findings.append(
                    Finding(
                        "error.handler_no_notify",
                        "medium",
                        et["name"],
                        "error workflow does not notify anyone (no Slack/email/chat/HTTP node after the trigger)",
                        "add a notification node; see references/patterns.md",
                    )
                )
    elif production and not target:
        findings.append(
            Finding(
                "error.no_error_workflow",
                "medium",
                "-",
                "production trigger without an error workflow: failures are only visible in the executions list",
                "Workflow settings > Error workflow > your alert workflow",
            )
        )
    if target:
        if target == wf.get("id"):
            findings.append(
                Finding(
                    "error.self_reference",
                    "info",
                    "-",
                    "workflow is its own error workflow",
                    "point it at a dedicated alert workflow",
                )
            )
        elif by_id:
            handler = by_id.get(str(target))
            if handler is None:
                findings.append(
                    Finding(
                        "error.unknown_error_workflow",
                        "low",
                        "-",
                        f"error workflow id '{target}' is not in the files given (deleted, or not exported?)",
                        "export it too, or pick an existing alert workflow",
                    )
                )
            elif not any(n.get("type") == ERROR_TRIGGER for n in handler.get("nodes") or []):
                findings.append(
                    Finding(
                        "error.target_not_error_trigger",
                        "high",
                        "-",
                        f"error workflow '{handler.get('name')}' has no Error Trigger node, so it never runs",
                        "start the error workflow with an Error Trigger node",
                    )
                )
    if settings.get("saveDataErrorExecution") == "none":
        findings.append(
            Finding(
                "error.failed_not_saved",
                "medium",
                "-",
                "failed executions are not saved",
                "set 'Save failed production executions' to Save",
            )
        )

    for n in nodes:
        name = n.get("name", "?")
        typ = n.get("type", "")
        params = n.get("parameters") or {}
        if n.get("disabled"):
            continue
        on_error = n.get("onError")
        if n.get("continueOnFail") or on_error == "continueRegularOutput":
            findings.append(
                Finding(
                    "error.swallowed",
                    "medium",
                    name,
                    "errors continue as normal items (On Error: Continue): later nodes process error objects",
                    "use 'Continue (using error output)' and handle that branch, or check $json.error right after",
                )
            )
        if on_error == "continueErrorOutput" and typ != "n8n-nodes-base.switch":
            idx = MULTI_OUTPUT.get(typ, 1)
            outs = outputs_of(wf, name)
            if len(outs) <= idx or not outs[idx]:
                findings.append(
                    Finding(
                        "error.error_output_unconnected",
                        "medium",
                        name,
                        "error output is not connected: failed items are dropped silently",
                        "connect the error output to a dead-letter or alert branch",
                    )
                )
        external = typ in ("n8n-nodes-base.httpRequest", "@n8n/n8n-nodes-langchain.toolHttpRequest") or bool(
            n.get("credentials")
        )
        if external and not is_trigger(n) and not typ.startswith(NO_RETRY_NEEDED_PREFIX) and not n.get("retryOnFail"):
            findings.append(
                Finding(
                    "error.no_retry",
                    "low",
                    name,
                    "calls an external service without Retry On Fail",
                    "Settings > Retry On Fail (3 tries, 2000-5000 ms) for transient errors",
                )
            )
        method = str(params.get("method", "GET")).upper()
        if (
            n.get("retryOnFail")
            and typ == "n8n-nodes-base.httpRequest"
            and method in ("POST", "PATCH")
            and not has_header(params, "idempotency-key")
        ):
            findings.append(
                Finding(
                    "error.retry_not_idempotent",
                    "info",
                    name,
                    f"{method} is retried without an Idempotency-Key header (a retry may create duplicates)",
                    "send Idempotency-Key with a stable id from the data (e.g. {{ $json.order_id }}) "
                    "if the API supports it",
                )
            )
    order = {s: i for i, s in enumerate(reversed(SEVERITIES))}
    findings.sort(key=lambda f: (order[f.severity], f.check, f.node))
    return findings


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Audit error handling across exported n8n workflows.",
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
    by_id = {str(w["id"]): w for _, w in workflows if w.get("id")}
    report = []
    worst = -1
    handlers: dict[str, list[str]] = {}
    for label, wf in workflows:
        fs = audit(wf, by_id)
        worst = max([worst, *(SEVERITIES.index(f.severity) for f in fs)])
        target = (wf.get("settings") or {}).get("errorWorkflow")
        if target:
            hname = by_id.get(str(target), {}).get("name", f"<missing {target}>")
            handlers.setdefault(hname, []).append(wf.get("name", label))
        report.append({"file": label, "workflow": wf.get("name", ""), "findings": [asdict(f) for f in fs]})
    if args.json:
        print(json.dumps({"files": report, "error_workflows": handlers}, indent=2))
    else:
        for r in report:
            print(f"== {r['file']} ({r['workflow']}): {len(r['findings'])} finding(s)")
            for f in r["findings"]:
                print(f"  {f['severity'].upper():6} {f['check']:30} [{f['node']}] {f['title']}")
                if f["fix"]:
                    print(f"         fix: {f['fix']}")
        if handlers:
            print("\nError workflow coverage:")
            for h, users in sorted(handlers.items()):
                print(f"  {h}: {', '.join(sorted(users))}")
    if args.fail_on == "none":
        return 0
    return 1 if worst >= SEVERITIES.index(args.fail_on) else 0


if __name__ == "__main__":
    sys.exit(main())
