#!/usr/bin/env python3
"""Extract and check every n8n expression in exported workflow JSON.

An n8n parameter is an expression when its string value starts with "=";
the JavaScript between {{ and }} is evaluated. This script lists those
expressions (--list) and reports common mistakes:

  expr.missing_equals   {{ }} in a plain string: sent literally, not evaluated
  expr.unbalanced       {{ without }} (or the reverse)
  expr.empty            {{ }} with nothing inside
  expr.python_syntax    and / or / not / True / False / None
  expr.statement        const / let / return inside {{ }} (must be one expression)
  expr.assignment       single = where == or === was meant
  expr.missing_node     $('Name') / $node["Name"] pointing at a node that does not exist
  expr.legacy_syntax    $node[...], $items(), $item() (old syntax)
  expr.invalid_dot_key  $json.first-name (minus, not a key) -> $json["first-name"]
  expr.jmespath_args    $jmespath('query', obj): arguments reversed
  expr.env_access       $env (blocked by default since n8n 2.0)
  expr.js_date          new Date() / Date.now() instead of $now / $today (timezone)
  code.item_in_all_mode $input.item / $json in a Code node that runs once for all items
  code.all_in_item_mode $input.all() in a Code node that runs once per item
  code.braces           {{ $json... }} inside Code: expressions are not evaluated there
  code.require          require() needs NODE_FUNCTION_ALLOW_BUILTIN / _EXTERNAL

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
CODE_KEYS = {"jsCode", "pythonCode", "functionCode", "code"}
CODE_TYPES = {"n8n-nodes-base.code", "n8n-nodes-base.function", "n8n-nodes-base.functionItem"}
STICKY = "n8n-nodes-base.stickyNote"
NODE_BUILTINS = {
    "crypto",
    "fs",
    "path",
    "os",
    "url",
    "util",
    "zlib",
    "buffer",
    "querystring",
    "http",
    "https",
    "child_process",
    "stream",
    "events",
}

STRING_RE = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`")
NODE_REF_RES = [
    re.compile(r"\$\(\s*(['\"`])(.+?)\1\s*\)"),
    re.compile(r"\$node\[\s*(['\"])(.+?)\1\s*\]"),
    re.compile(r"\$items\(\s*(['\"])(.+?)\1"),
]
NODE_DOT_RE = re.compile(r"\$node\.([A-Za-z_$][\w$]*)")
PY_RE = re.compile(r"(?<![.\w$])(and|or|not|True|False|None|elif)(?![\w$])")
STATEMENT_RE = re.compile(r"(?<![.\w$])(const|let|var|return)\s")
ASSIGN_RE = re.compile(r"(?<![=!<>+\-*/%&|^?])=(?![=>])")
DOT_DASH_RE = re.compile(
    r"(\$json|\.json|\$input\.item\.json)((?:\.[A-Za-z_$][\w$]*)*)\.([A-Za-z_$][\w$]*)-([A-Za-z_][\w]*)"
)
JMES_RE = re.compile(r"\$jmespath\(\s*(['\"`])")


@dataclass
class Finding:
    check: str
    severity: str
    node: str
    param: str
    title: str
    fix: str = ""


# --------------------------------------------------------------------------- loading


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


def iter_strings(value, path: str = ""):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from iter_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from iter_strings(v, f"{path}[{i}]")


# --------------------------------------------------------------------------- parsing


def segments(expr: str) -> tuple[list[str], bool]:
    """Return the JavaScript snippets between {{ and }} and whether braces balance."""
    out: list[str] = []
    i = 0
    balanced = True
    while True:
        start = expr.find("{{", i)
        if start == -1:
            if "}}" in expr[i:]:
                balanced = False
            break
        if "}}" in expr[i:start]:
            balanced = False
        end = expr.find("}}", start + 2)
        if end == -1:
            balanced = False
            break
        # allow object literals like {{ {a: 1} }}: extend while inner braces are open
        while expr[start + 2 : end].count("{") > expr[start + 2 : end].count("}"):
            nxt = expr.find("}}", end + 1)
            if nxt == -1:
                break
            end = nxt
        out.append(expr[start + 2 : end])
        i = end + 2
    return out, balanced


def strip_strings(js: str) -> str:
    return STRING_RE.sub('""', js)


def referenced_nodes(js: str) -> list[str]:
    names = []
    for rx in NODE_REF_RES:
        names += [m.group(2) for m in rx.finditer(js)]
    names += NODE_DOT_RE.findall(js)
    return names


def check_js(js: str, node_names: set[str]) -> list[tuple[str, str, str, str]]:
    """Return (check, severity, title, fix) for one {{ }} segment."""
    out = []
    bare = strip_strings(js)
    if not js.strip():
        out.append(("expr.empty", "medium", "empty {{ }}", "remove it or fill in the expression"))
        return out
    py = sorted(set(PY_RE.findall(bare)))
    if py:
        out.append(
            (
                "expr.python_syntax",
                "high",
                f"Python syntax in a JavaScript expression: {', '.join(py)}",
                "use && || ! true false null",
            )
        )
    is_fn = "=>" in bare or re.search(r"\bfunction\b", bare)
    if STATEMENT_RE.search(bare) and not is_fn:
        out.append(
            (
                "expr.statement",
                "high",
                "statement inside {{ }}: an expression must be a single JavaScript expression",
                "use a ternary, an IIFE {{ (() => { ... })() }} or a Code node",
            )
        )
    elif ASSIGN_RE.search(bare) and not is_fn:
        out.append(("expr.assignment", "medium", "single '=' inside an expression", "compare with === (or ==)"))
    for ref in referenced_nodes(js):
        if ref not in node_names:
            out.append(
                (
                    "expr.missing_node",
                    "high",
                    f"refers to node '{ref}' which does not exist (renamed?)",
                    "update the name in the expression; n8n only renames references made in the editor",
                )
            )
    legacy = [s for s in ("$node[", "$node.", "$items(", "$item(") if s in bare]
    if legacy:
        out.append(
            (
                "expr.legacy_syntax",
                "info",
                f"legacy syntax {', '.join(legacy)}",
                "use $('Node name').item.json / .first() / .all()",
            )
        )
    m = DOT_DASH_RE.search(bare)
    if m:
        key = f"{m.group(3)}-{m.group(4)}"
        out.append(
            (
                "expr.invalid_dot_key",
                "medium",
                f"'.{key}' is parsed as a subtraction, not a key",
                f'use bracket notation: ["{key}"]',
            )
        )
    if JMES_RE.search(js):
        out.append(
            (
                "expr.jmespath_args",
                "medium",
                "$jmespath() called with the query first",
                "argument order is $jmespath(object, 'query')",
            )
        )
    if "$env" in bare:
        out.append(
            (
                "expr.env_access",
                "low",
                "$env access is blocked by default since n8n 2.0 (N8N_BLOCK_ENV_ACCESS_IN_NODE)",
                "use credentials or $vars instead of environment variables",
            )
        )
    if re.search(r"new Date\(|Date\.now\(\)", bare):
        out.append(
            (
                "expr.js_date",
                "info",
                "JavaScript Date ignores the workflow timezone",
                "use $now / $today (Luxon), e.g. $now.minus({ days: 1 }).toISODate()",
            )
        )
    return out


def check_code(node: dict) -> list[tuple[str, str, str, str, str]]:
    params = node.get("parameters") or {}
    out = []
    js = params.get("jsCode") or params.get("functionCode") or ""
    if not isinstance(js, str) or not js:
        return out
    mode = params.get("mode", "runOnceForAllItems")
    bare = strip_strings(js)
    if mode == "runOnceForAllItems" and node.get("type") == "n8n-nodes-base.code":
        if re.search(r"\$input\.item\b", bare) or re.search(r"(?<![\w.])\$json\b", bare):
            out.append(
                (
                    "jsCode",
                    "code.item_in_all_mode",
                    "high",
                    "uses $input.item / $json but the node runs once for ALL items",
                    "loop over $input.all(), or set Mode to 'Run Once for Each Item'",
                )
            )
    if mode == "runOnceForEachItem" and re.search(r"\$input\.(all|first|last)\(\)", bare):
        out.append(
            (
                "jsCode",
                "code.all_in_item_mode",
                "low",
                "reads $input.all()/first() while running once per item (repeats work n times)",
                "switch Mode to 'Run Once for All Items' or use $input.item",
            )
        )
    if re.search(r"\{\{\s*\$", js):
        out.append(
            (
                "jsCode",
                "code.braces",
                "medium",
                "{{ }} expression syntax inside a Code node is not evaluated",
                "write plain JavaScript: $json.field, $('Node').first().json",
            )
        )
    mods = sorted(set(re.findall(r"require\(\s*['\"]([^'\"]+)['\"]\s*\)", js)))
    if mods:
        builtin = [m for m in mods if m.split("/")[0].replace("node:", "") in NODE_BUILTINS]
        external = [m for m in mods if m not in builtin]
        parts = []
        if builtin:
            parts.append(f"NODE_FUNCTION_ALLOW_BUILTIN={','.join(builtin)}")
        if external:
            parts.append(f"NODE_FUNCTION_ALLOW_EXTERNAL={','.join(external)}")
        out.append(
            (
                "jsCode",
                "code.require",
                "info",
                f"require({', '.join(mods)}) must be allowed on the instance / task runner",
                "set " + " and ".join(parts),
            )
        )
    if "$env" in bare:
        out.append(
            (
                "jsCode",
                "expr.env_access",
                "low",
                "$env access is blocked by default since n8n 2.0 (N8N_BLOCK_ENV_ACCESS_IN_NODE)",
                "use credentials instead of environment variables",
            )
        )
    return out


# --------------------------------------------------------------------------- main checks


def collect(wf: dict) -> tuple[list[dict], list[Finding]]:
    nodes = [n for n in wf.get("nodes") or [] if isinstance(n, dict)]
    names = {n.get("name", "") for n in nodes}
    expressions: list[dict] = []
    findings: list[Finding] = []
    for n in nodes:
        if n.get("type") == STICKY:
            continue
        name = n.get("name", "?")
        for path, value in iter_strings(n.get("parameters") or {}):
            if path.split(".")[-1] in CODE_KEYS:
                continue
            if value.startswith("="):
                expr = value[1:]
                segs, balanced = segments(expr)
                expressions.append({"node": name, "param": path, "expression": expr, "segments": segs})
                if not balanced:
                    findings.append(
                        Finding(
                            "expr.unbalanced",
                            "high",
                            name,
                            path,
                            "unbalanced {{ }}",
                            "every {{ needs a closing }}",
                        )
                    )
                for seg in segs:
                    for check, sev, title, fix in check_js(seg, names):
                        findings.append(Finding(check, sev, name, path, title, fix))
            elif re.search(r"\{\{.+?\}\}", value, re.S):
                findings.append(
                    Finding(
                        "expr.missing_equals",
                        "medium",
                        name,
                        path,
                        "contains {{ }} but is a fixed value, so it is sent literally",
                        "switch the field to Expression (the JSON value must start with '=')",
                    )
                )
        if n.get("type") in CODE_TYPES:
            for param, check, sev, title, fix in check_code(n):
                findings.append(Finding(check, sev, name, param, title, fix))
    # de-duplicate identical findings on the same parameter
    uniq: dict[tuple, Finding] = {}
    for f in findings:
        uniq.setdefault((f.check, f.node, f.param, f.title), f)
    order = {s: i for i, s in enumerate(reversed(SEVERITIES))}
    result = sorted(uniq.values(), key=lambda f: (order[f.severity], f.node, f.param, f.check))
    return expressions, result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Extract and check n8n expressions in exported workflow JSON.",
        epilog="Exit codes: 0 = clean, 1 = findings at or above --fail-on, 2 = error.",
    )
    p.add_argument("paths", nargs="+", help="workflow .json files or directories, or - for stdin")
    p.add_argument("--list", action="store_true", help="also print every expression found")
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
        exprs, findings = collect(wf)
        worst = max([worst, *(SEVERITIES.index(f.severity) for f in findings)])
        entry = {"file": label, "workflow": wf.get("name", ""), "expression_count": len(exprs)}
        if args.list:
            entry["expressions"] = exprs
        entry["findings"] = [asdict(f) for f in findings]
        report.append(entry)
    if args.json:
        print(json.dumps({"files": report}, indent=2))
    else:
        for r in report:
            print(
                f"== {r['file']} ({r['workflow']}): {r['expression_count']} expression(s), "
                f"{len(r['findings'])} finding(s)"
            )
            for e in r.get("expressions", []):
                print(f"  EXPR [{e['node']}] {e['param']}: {e['expression'][:120]}")
            for f in r["findings"]:
                print(f"  {f['severity'].upper():6} {f['check']:22} [{f['node']}] {f['param']}: {f['title']}")
                if f["fix"]:
                    print(f"         fix: {f['fix']}")
    if args.fail_on == "none":
        return 0
    return 1 if worst >= SEVERITIES.index(args.fail_on) else 0


if __name__ == "__main__":
    sys.exit(main())
