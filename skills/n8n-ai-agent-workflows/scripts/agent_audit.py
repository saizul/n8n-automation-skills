#!/usr/bin/env python3
"""Audit AI Agent nodes in exported n8n workflows: tools, memory, model, cost.

For every AI Agent (and Basic LLM Chain) node it maps the connected chat
model, memory, tools and output parser, then reports:

  agent.no_model            no chat model connected
  agent.no_system_message   default system prompt
  agent.max_iterations      maxIterations above --max-iterations (runaway tool loops)
  agent.no_tools            agent without tools (a Basic LLM Chain is one call, cheaper)
  agent.too_many_tools      more tools than --max-tools (worse tool choice, longer prompts)
  agent.parser_missing      "Require specific output format" on, no output parser attached
  agent.public_entry        public, unauthenticated Chat Trigger in front of the agent
  tool.no_description       tool without a description (the model cannot know when to use it)
  tool.short_description    description under 40 characters
  tool.default_name         tool keeps a default node name (the model sees it as the tool name)
  tool.side_effects         tool that deletes, sends, pays or changes data: add an approval step
  memory.shared_session     memory session key is a fixed value: every user shares one history
  memory.window_large       memory window above --max-window turns (tokens grow every turn)
  model.no_max_tokens       no output token limit on the chat model
  model.high_temperature    temperature above 1

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
LC = "@n8n/n8n-nodes-langchain."
AGENT_TYPES = {LC + "agent", LC + "chainLlm"}
SIDE_EFFECT_RE = re.compile(
    r"\b(delete|remove|drop|refund|cancel|charge|pay|transfer|send|email|post|publish|update|write|create|insert)",
    re.I,
)
DESCRIPTION_KEYS = ("toolDescription", "description")


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


def attached(wf: dict, target: str) -> dict[str, list[str]]:
    """Return {connection_type: [source node names]} for sub-nodes attached to target."""
    out: dict[str, list[str]] = {}
    for src, by_type in (wf.get("connections") or {}).items():
        for ctype, outputs in (by_type or {}).items():
            if not ctype.startswith("ai_"):
                continue
            for targets in outputs or []:
                for t in targets or []:
                    if t.get("node") == target:
                        out.setdefault(ctype, []).append(src)
    return out


def main_parents(wf: dict, target: str) -> list[str]:
    parents = []
    for src, by_type in (wf.get("connections") or {}).items():
        for targets in (by_type or {}).get("main") or []:
            if any(t.get("node") == target for t in targets or []):
                parents.append(src)
    return parents


def model_name(params: dict) -> str:
    m = params.get("model") or params.get("modelName") or ""
    if isinstance(m, dict):
        m = m.get("value") or m.get("cachedResultName") or ""
    return str(m)


def tool_description(node: dict) -> tuple[str, bool]:
    """Return (description, auto) where auto means n8n generates it."""
    params = node.get("parameters") or {}
    if params.get("descriptionType") == "auto":
        return "", True
    for key in DESCRIPTION_KEYS:
        if isinstance(params.get(key), str) and params[key].strip():
            return params[key].strip(), False
    return "", False


def is_default_tool_name(node: dict) -> bool:
    name = re.sub(r"\d+$", "", node.get("name", "")).strip().lower()
    short = node.get("type", "").rsplit(".", 1)[-1].lower()
    norm = re.sub(r"[^a-z0-9]", "", name)
    base = re.sub(r"^tool|tool$", "", short)
    defaults = {
        short,
        base,
        "calln8nworkflowtool",
        "callan8nworkflowtool",
        "codetool",
        "httprequest",
        "httprequesttool",
        "workflowtool",
    }
    return norm in defaults


def audit(
    wf: dict, max_iterations: int = 15, max_tools: int = 10, max_window: int = 20
) -> tuple[list[dict], list[Finding]]:
    nodes = {n.get("name"): n for n in wf.get("nodes") or [] if isinstance(n, dict)}
    findings: list[Finding] = []
    agents = []
    for name, node in nodes.items():
        if node.get("type") not in AGENT_TYPES or node.get("disabled"):
            continue
        params = node.get("parameters") or {}
        options = params.get("options") or {}
        subs = attached(wf, name)
        models = [nodes[m] for m in subs.get("ai_languageModel", []) if m in nodes]
        memories = [nodes[m] for m in subs.get("ai_memory", []) if m in nodes]
        tools = [nodes[t] for t in subs.get("ai_tool", []) if t in nodes]
        parsers = subs.get("ai_outputParser", [])
        is_agent = node.get("type") == LC + "agent"
        iterations = options.get("maxIterations", 10)
        summary = {
            "agent": name,
            "type": "agent" if is_agent else "llm-chain",
            "models": [f"{m.get('name')} ({model_name(m.get('parameters') or {}) or m.get('type')})" for m in models],
            "memory": [m.get("name") for m in memories],
            "tools": [t.get("name") for t in tools],
            "max_iterations": iterations if is_agent else None,
            "output_parser": parsers,
        }
        agents.append(summary)

        if not models:
            findings.append(Finding("agent.no_model", "high", name, "no chat model connected", "attach a Chat Model"))
        if is_agent and not str(options.get("systemMessage", "")).strip():
            findings.append(
                Finding(
                    "agent.no_system_message",
                    "medium",
                    name,
                    "uses the default system message",
                    "write a system message: role, scope, which tool for what, refusal and handoff rules",
                )
            )
        if is_agent and isinstance(iterations, (int, float)) and iterations > max_iterations:
            findings.append(
                Finding(
                    "agent.max_iterations",
                    "medium",
                    name,
                    f"maxIterations is {iterations} (limit {max_iterations}): "
                    "a confused agent can loop and burn tokens",
                    "keep it at 5-10; fix tool descriptions instead of raising it",
                )
            )
        if is_agent and not tools:
            findings.append(
                Finding(
                    "agent.no_tools",
                    "info",
                    name,
                    "agent has no tools",
                    "a Basic LLM Chain does the same in one model call",
                )
            )
        if len(tools) > max_tools:
            findings.append(
                Finding(
                    "agent.too_many_tools",
                    "low",
                    name,
                    f"{len(tools)} tools (limit {max_tools})",
                    "split into specialised agents (agent-as-tool) or merge overlapping tools",
                )
            )
        if params.get("hasOutputParser") and not parsers:
            findings.append(
                Finding(
                    "agent.parser_missing",
                    "high",
                    name,
                    "'Require Specific Output Format' is on but no output parser is attached",
                    "attach a Structured Output Parser or turn the option off",
                )
            )
        for parent in main_parents(wf, name):
            pnode = nodes.get(parent) or {}
            pp = pnode.get("parameters") or {}
            if (
                pnode.get("type") == LC + "chatTrigger"
                and pp.get("public")
                and pp.get("authentication", "none") == "none"
            ):
                findings.append(
                    Finding(
                        "agent.public_entry",
                        "medium",
                        parent,
                        "public Chat Trigger without authentication feeds this agent",
                        "add authentication and a per-session message limit (see references/cost-control.md)",
                    )
                )
        for t in tools:
            tname = t.get("name", "?")
            desc, auto = tool_description(t)
            if not desc and not auto:
                findings.append(
                    Finding(
                        "tool.no_description",
                        "high",
                        tname,
                        "tool has no description, so the model guesses when and how to call it",
                        "describe what it does, when to use it, the input format and what it returns",
                    )
                )
            elif desc and len(desc) < 40:
                findings.append(
                    Finding(
                        "tool.short_description",
                        "low",
                        tname,
                        f"description is only {len(desc)} characters",
                        "add when to use it, the input fields and an example",
                    )
                )
            if is_default_tool_name(t):
                findings.append(
                    Finding(
                        "tool.default_name",
                        "low",
                        tname,
                        "tool keeps a default name; the model sees the node name as the tool name",
                        "rename to a verb_noun like lookup_order or create_ticket",
                    )
                )
            if SIDE_EFFECT_RE.search(tname) or (desc and SIDE_EFFECT_RE.search(desc.split(".")[0])):
                findings.append(
                    Finding(
                        "tool.side_effects",
                        "medium",
                        tname,
                        "tool changes data or contacts people: the model can call it on a bad guess",
                        "require human approval for it, or have it create a draft/request instead of acting",
                    )
                )
        for m in memories:
            mp = m.get("parameters") or {}
            key = mp.get("sessionKey")
            if mp.get("sessionIdType") == "customKey" and (not isinstance(key, str) or not key.startswith("=")):
                findings.append(
                    Finding(
                        "memory.shared_session",
                        "high",
                        m.get("name", "?"),
                        "session key is a fixed value: every user reads and writes the same conversation",
                        "use an expression per user/conversation, e.g. ={{ $json.sessionId }}",
                    )
                )
            window = mp.get("contextWindowLength", 5)
            if isinstance(window, (int, float)) and window > max_window:
                findings.append(
                    Finding(
                        "memory.window_large",
                        "low",
                        m.get("name", "?"),
                        f"memory keeps {window} turns (limit {max_window}); every call resends them",
                        "keep 5-10 turns, or summarise older turns",
                    )
                )
        for m in models:
            mopts = (m.get("parameters") or {}).get("options") or {}
            if not any(re.search(r"max.*tokens", k, re.I) for k in mopts):
                findings.append(
                    Finding(
                        "model.no_max_tokens",
                        "low",
                        m.get("name", "?"),
                        "no maximum output tokens set",
                        "set Maximum Number of Tokens to the longest answer you need",
                    )
                )
            temp = mopts.get("temperature")
            if isinstance(temp, (int, float)) and temp > 1:
                findings.append(
                    Finding(
                        "model.high_temperature",
                        "low",
                        m.get("name", "?"),
                        f"temperature {temp}: tool arguments get unreliable",
                        "use 0-0.3 for tool-using agents",
                    )
                )
    uniq: dict[tuple, Finding] = {}
    for f in findings:
        uniq.setdefault((f.check, f.node), f)
    order = {s: i for i, s in enumerate(reversed(SEVERITIES))}
    return agents, sorted(uniq.values(), key=lambda f: (order[f.severity], f.check, f.node))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Audit AI Agent nodes (model, memory, tools, cost) in exported n8n workflows.",
        epilog="Exit codes: 0 = clean, 1 = findings at or above --fail-on, 2 = error.",
    )
    p.add_argument("paths", nargs="+", help="workflow .json files or directories, or - for stdin")
    p.add_argument("--json", action="store_true", help="print JSON")
    p.add_argument("--fail-on", default="medium", choices=[*SEVERITIES, "none"])
    p.add_argument("--max-iterations", type=int, default=15)
    p.add_argument("--max-tools", type=int, default=10)
    p.add_argument("--max-window", type=int, default=20, help="memory turns")
    args = p.parse_args(argv)
    try:
        workflows = load_workflows(args.paths)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    report = []
    worst = -1
    for label, wf in workflows:
        agents, fs = audit(wf, args.max_iterations, args.max_tools, args.max_window)
        if not agents:
            continue
        worst = max([worst, *(SEVERITIES.index(f.severity) for f in fs)])
        report.append(
            {
                "file": label,
                "workflow": wf.get("name", ""),
                "agents": agents,
                "findings": [asdict(f) for f in fs],
            }
        )
    if args.json:
        print(json.dumps({"files": report}, indent=2))
    else:
        if not report:
            print("no AI Agent or LLM Chain nodes found")
        for r in report:
            print(f"== {r['file']} ({r['workflow']})")
            for a in r["agents"]:
                print(
                    f"  AGENT [{a['agent']}] model={', '.join(a['models']) or '-'} "
                    f"memory={', '.join(a['memory']) or '-'} tools={', '.join(a['tools']) or '-'} "
                    f"maxIterations={a['max_iterations']}"
                )
            for f in r["findings"]:
                print(f"  {f['severity'].upper():6} {f['check']:24} [{f['node']}] {f['title']}")
                if f["fix"]:
                    print(f"         fix: {f['fix']}")
    if args.fail_on == "none":
        return 0
    return 1 if worst >= SEVERITIES.index(args.fail_on) else 0


if __name__ == "__main__":
    sys.exit(main())
