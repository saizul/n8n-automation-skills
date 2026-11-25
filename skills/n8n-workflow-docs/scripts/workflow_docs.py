#!/usr/bin/env python3
"""Generate Markdown documentation with a Mermaid diagram from n8n workflow JSON.

For each workflow: summary (status, id, tags, error workflow, timezone),
triggers (webhook method/path/auth, schedules, chat/form/error triggers),
a Mermaid flowchart (branches labelled true/false, loop/done, error; AI
sub-nodes dotted), a node table, credentials used (type and name only, never
values), external hosts, and sub-workflows called.

Output goes to stdout, or with --out DIR one <workflow>.md per workflow plus
an index README.md. Read-only on the input files.

Exit codes: 0 = ok, 2 = error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

STICKY = "n8n-nodes-base.stickyNote"
DECISIONS = {"n8n-nodes-base.if", "n8n-nodes-base.switch", "n8n-nodes-base.filter"}
OUTPUT_LABELS = {
    "n8n-nodes-base.if": ["true", "false"],
    "n8n-nodes-base.splitInBatches": ["done", "loop"],
    "n8n-nodes-base.filter": ["kept", "discarded"],
    "n8n-nodes-base.compareDatasets": ["in A only", "same", "different", "in B only"],
}
AI_LABELS = {
    "ai_languageModel": "model",
    "ai_memory": "memory",
    "ai_tool": "tool",
    "ai_outputParser": "parser",
    "ai_embedding": "embedding",
    "ai_vectorStore": "vector store",
    "ai_document": "document",
    "ai_textSplitter": "splitter",
    "ai_retriever": "retriever",
}
URL_RE = re.compile(r"https?://[^\s'\"`)}]+")


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


def short_type(t: str) -> str:
    return t.rsplit(".", 1)[-1]


def is_trigger(node: dict) -> bool:
    s = short_type(node.get("type", "")).lower()
    return s.endswith("trigger") or s in {"webhook", "start"}


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "workflow"


def md_escape(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def mermaid_label(s: str) -> str:
    return str(s).replace('"', "#quot;").replace("<", "#lt;").replace(">", "#gt;")


def all_edges(wf: dict):
    for src, by_type in (wf.get("connections") or {}).items():
        for ctype, outputs in (by_type or {}).items():
            for idx, targets in enumerate(outputs or []):
                for t in targets or []:
                    if isinstance(t, dict) and "node" in t:
                        yield src, t["node"], idx, ctype


def ordered_nodes(wf: dict) -> list[dict]:
    nodes = [n for n in wf.get("nodes") or [] if isinstance(n, dict) and n.get("type") != STICKY]
    by_name = {n.get("name"): n for n in nodes}
    adj: dict[str, list[str]] = {}
    for s, d, _, ctype in all_edges(wf):
        if ctype == "main":
            adj.setdefault(s, []).append(d)
    pos = {n.get("name"): tuple(n.get("position") or [0, 0]) for n in nodes}
    order: list[str] = []
    queue = sorted((n.get("name") for n in nodes if is_trigger(n)), key=lambda x: pos.get(x, (0, 0)))
    seen = set(queue)
    while queue:
        cur = queue.pop(0)
        order.append(cur)
        for nxt in adj.get(cur, []):
            if nxt not in seen and nxt in by_name:
                seen.add(nxt)
                queue.append(nxt)
    rest = sorted((n.get("name") for n in nodes if n.get("name") not in seen), key=lambda x: pos.get(x, (0, 0)))
    return [by_name[n] for n in order + rest]


def output_label(node: dict, idx: int) -> str:
    typ = node.get("type", "")
    if node.get("onError") == "continueErrorOutput":
        normal = len(OUTPUT_LABELS.get(typ, ["ok"]))
        if typ != "n8n-nodes-base.switch" and idx == normal:
            return "error"
    labels = OUTPUT_LABELS.get(typ)
    if labels and idx < len(labels):
        return labels[idx]
    if typ == "n8n-nodes-base.switch":
        return f"output {idx}"
    return ""


def mermaid(wf: dict) -> str:
    nodes = ordered_nodes(wf)
    ids = {n.get("name"): f"n{i}" for i, n in enumerate(nodes)}
    by_name = {n.get("name"): n for n in nodes}
    lines = ["flowchart LR"]
    for n in nodes:
        nid = ids[n["name"]]
        label = f"{mermaid_label(n['name'])}<br/><small>{mermaid_label(short_type(n.get('type', '')))}</small>"
        if is_trigger(n):
            shape = f'{nid}(["{label}"])'
        elif n.get("type") in DECISIONS:
            shape = f'{nid}{{"{label}"}}'
        else:
            shape = f'{nid}["{label}"]'
        if n.get("disabled"):
            shape += ":::disabled"
        lines.append(f"  {shape}")
    for s, d, idx, ctype in all_edges(wf):
        if s not in ids or d not in ids:
            continue
        if ctype == "main":
            lab = output_label(by_name[s], idx)
            arrow = f"-->|{mermaid_label(lab)}|" if lab else "-->"
            lines.append(f"  {ids[s]} {arrow} {ids[d]}")
        else:
            lab = AI_LABELS.get(ctype, ctype.replace("ai_", ""))
            lines.append(f"  {ids[s]} -.->|{lab}| {ids[d]}")
    lines.append("  classDef disabled stroke-dasharray: 5 5,opacity:0.5")
    return "\n".join(lines)


def trigger_detail(n: dict) -> str:
    p = n.get("parameters") or {}
    typ = n.get("type", "")
    if typ == "n8n-nodes-base.webhook":
        return (
            f"{p.get('httpMethod', 'GET')} `/webhook/{p.get('path', '')}`, auth: {p.get('authentication', 'none')}, "
            f"respond: {p.get('responseMode', 'onReceived')}"
        )
    if typ == "n8n-nodes-base.scheduleTrigger":
        rules = (p.get("rule") or {}).get("interval") or []
        parts = []
        for r in rules:
            if r.get("field") == "cronExpression":
                parts.append(f"cron `{r.get('expression')}`")
            else:
                field = r.get("field", "days")
                every = r.get(f"{field}Interval", 1)
                at = (
                    f" at {r['triggerAtHour']:02d}:{int(r.get('triggerAtMinute', 0)):02d}"
                    if "triggerAtHour" in r
                    else ""
                )
                parts.append(f"every {every} {field}{at}")
        return "; ".join(parts) or "schedule"
    if typ == "@n8n/n8n-nodes-langchain.chatTrigger":
        return "public chat" if p.get("public") else "chat (editor / embedded)"
    if typ == "n8n-nodes-base.errorTrigger":
        return "runs when a workflow that uses this one as its error workflow fails"
    if typ == "n8n-nodes-base.executeWorkflowTrigger":
        return "called by other workflows (Execute Workflow)"
    if typ == "n8n-nodes-base.manualTrigger":
        return "manual run from the editor"
    if typ == "n8n-nodes-base.formTrigger":
        return f"form `{p.get('formTitle', '')}`, auth: {p.get('authentication', 'none')}"
    return short_type(typ)


def node_notes(n: dict) -> str:
    bits = []
    if n.get("disabled"):
        bits.append("**disabled**")
    if n.get("retryOnFail"):
        bits.append(f"retry x{n.get('maxTries', 3)}")
    if n.get("onError"):
        bits.append(f"on error: {n['onError']}")
    if n.get("continueOnFail"):
        bits.append("continue on fail")
    if n.get("executeOnce"):
        bits.append("execute once")
    for ctype, cred in (n.get("credentials") or {}).items():
        bits.append(f"credential: {cred.get('name', ctype) if isinstance(cred, dict) else ctype}")
    if n.get("notes"):
        bits.append(str(n["notes"]))
    return "; ".join(bits)


def collect(wf: dict) -> dict:
    nodes = ordered_nodes(wf)
    creds = {}
    hosts = set()
    subs = []
    for n in nodes:
        for ctype, cred in (n.get("credentials") or {}).items():
            cname = cred.get("name", "") if isinstance(cred, dict) else ""
            creds[(ctype, cname)] = creds.get((ctype, cname), []) + [n["name"]]
        p = n.get("parameters") or {}
        url = p.get("url")
        if isinstance(url, str):
            for u in URL_RE.findall(url):
                host = urlparse(u).hostname
                if host:
                    hosts.add(host)
        if n.get("type") in ("n8n-nodes-base.executeWorkflow", "@n8n/n8n-nodes-langchain.toolWorkflow"):
            wid = p.get("workflowId")
            if isinstance(wid, dict):
                subs.append((n["name"], wid.get("cachedResultName") or "", str(wid.get("value", ""))))
            elif wid:
                subs.append((n["name"], "", str(wid)))
    settings = wf.get("settings") or {}
    return {
        "name": wf.get("name", "(unnamed)"),
        "id": wf.get("id", ""),
        "active": bool(wf.get("active")),
        "tags": [t.get("name", t) if isinstance(t, dict) else t for t in wf.get("tags") or []],
        "error_workflow": settings.get("errorWorkflow", ""),
        "timezone": settings.get("timezone", ""),
        "execution_order": settings.get("executionOrder", ""),
        "triggers": [
            {"node": n["name"], "type": short_type(n["type"]), "detail": trigger_detail(n)}
            for n in nodes
            if is_trigger(n)
        ],
        "nodes": [{"name": n["name"], "type": short_type(n.get("type", "")), "notes": node_notes(n)} for n in nodes],
        "credentials": [{"type": t, "name": nm, "used_by": u} for (t, nm), u in sorted(creds.items())],
        "hosts": sorted(hosts),
        "sub_workflows": [{"node": a, "name": b, "id": c} for a, b, c in subs],
        "sticky_notes": [
            str((n.get("parameters") or {}).get("content", "")).strip()
            for n in wf.get("nodes") or []
            if n.get("type") == STICKY and (n.get("parameters") or {}).get("content")
        ],
        "mermaid": mermaid(wf),
    }


def render(info: dict, source: str) -> str:
    out = [f"# {info['name']}", ""]
    out.append(f"_Generated from `{Path(source).name}` by workflow_docs.py. Regenerate after changes._")
    out.append("")
    out.append("| | |")
    out.append("| --- | --- |")
    out.append(f"| Status | {'active' if info['active'] else 'inactive'} |")
    if info["id"]:
        out.append(f"| ID | `{info['id']}` |")
    if info["tags"]:
        out.append(f"| Tags | {', '.join(info['tags'])} |")
    out.append(f"| Nodes | {len(info['nodes'])} |")
    out.append(f"| Error workflow | {('`' + info['error_workflow'] + '`') if info['error_workflow'] else '**none**'} |")
    if info["timezone"]:
        out.append(f"| Timezone | {info['timezone']} |")
    out.append("")
    if info["sticky_notes"]:
        out += ["## Notes from the canvas", ""]
        for note in info["sticky_notes"]:
            out += ["> " + line for line in note.splitlines()] + [""]
    out += ["## Triggers", ""]
    if info["triggers"]:
        out += [f"- **{md_escape(t['node'])}** ({t['type']}): {t['detail']}" for t in info["triggers"]]
    else:
        out.append("- none (runs only when called or started manually)")
    out += ["", "## Flow", "", "```mermaid", info["mermaid"], "```", "", "## Nodes", ""]
    out += ["| # | Node | Type | Notes |", "| --- | --- | --- | --- |"]
    for i, n in enumerate(info["nodes"], 1):
        out.append(f"| {i} | {md_escape(n['name'])} | `{n['type']}` | {md_escape(n['notes'])} |")
    out += ["", "## Credentials", ""]
    if info["credentials"]:
        out += [
            f"- `{c['type']}`: {md_escape(c['name']) or '(unnamed)'} (used by {', '.join(c['used_by'])})"
            for c in info["credentials"]
        ]
    else:
        out.append("- none")
    if info["hosts"]:
        out += ["", "## External hosts (hard-coded URLs)", ""] + [f"- {h}" for h in info["hosts"]]
    if info["sub_workflows"]:
        out += ["", "## Sub-workflows called", ""]
        out += [f"- {md_escape(s['node'])} -> {s['name'] or '(id)'} `{s['id']}`" for s in info["sub_workflows"]]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Generate Markdown docs and Mermaid diagrams from n8n workflow JSON.",
        epilog="Exit codes: 0 = ok, 2 = error.",
    )
    p.add_argument("paths", nargs="+", help="workflow .json files or directories, or - for stdin")
    p.add_argument("--out", type=Path, help="write one .md per workflow plus README.md into this directory")
    p.add_argument("--mermaid-only", action="store_true", help="print only the Mermaid diagram(s)")
    p.add_argument("--json", action="store_true", help="print the collected structure as JSON")
    args = p.parse_args(argv)
    try:
        workflows = load_workflows(args.paths)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    infos = [(label, collect(wf)) for label, wf in workflows]
    if args.json:
        print(json.dumps([dict(info, file=label) for label, info in infos], indent=2))
        return 0
    if args.mermaid_only:
        for _, info in infos:
            print(f"%% {info['name']}\n{info['mermaid']}\n")
        return 0
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        index = ["# Workflows", "", "| Workflow | Status | Triggers | Nodes |", "| --- | --- | --- | --- |"]
        used: set[str] = set()
        for label, info in infos:
            name = slug(info["name"])
            while name in used:
                name += "-1"
            used.add(name)
            (args.out / f"{name}.md").write_text(render(info, label.split("#")[0]), encoding="utf-8")
            trig = ", ".join(t["type"] for t in info["triggers"]) or "-"
            index.append(
                f"| [{md_escape(info['name'])}]({name}.md) | {'active' if info['active'] else 'inactive'} "
                f"| {trig} | {len(info['nodes'])} |"
            )
        (args.out / "README.md").write_text("\n".join(index) + "\n", encoding="utf-8")
        print(f"wrote {len(infos)} workflow doc(s) and README.md to {args.out}")
        return 0
    print("\n".join(render(info, label.split("#")[0]) for label, info in infos))
    return 0


if __name__ == "__main__":
    sys.exit(main())
