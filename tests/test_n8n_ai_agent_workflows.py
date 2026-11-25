import json

import pytest

from conftest import WORKFLOWS, load_script, load_wf, make_wf, run_script

SKILL = "n8n-ai-agent-workflows"
SCRIPT = "agent_audit.py"
mod = load_script(SKILL, SCRIPT)
LC = "@n8n/n8n-nodes-langchain."


def ai(src, kind, target="Agent"):
    return {src: {kind: [[{"node": target, "type": kind, "index": 0}]]}}


def checks(wf, **kw):
    return {(f.check, f.node) for f in mod.audit(wf, **kw)[1]}


def test_bad_agent():
    agents, findings = mod.audit(load_wf("ai-support-agent-bad.json"))
    assert agents[0]["tools"] == ["HTTP Request", "Delete customer"]
    assert agents[0]["max_iterations"] == 50
    assert agents[0]["models"] == ["OpenAI Chat Model (gpt-4o)"]
    got = {(f.check, f.node) for f in findings}
    expected = {
        ("tool.no_description", "HTTP Request"),
        ("tool.default_name", "HTTP Request"),
        ("tool.side_effects", "Delete customer"),
        ("tool.short_description", "Delete customer"),
        ("agent.max_iterations", "AI Agent"),
        ("agent.no_system_message", "AI Agent"),
        ("agent.public_entry", "When chat message received"),
        ("memory.window_large", "Window Buffer Memory"),
        ("model.no_max_tokens", "OpenAI Chat Model"),
        ("model.high_temperature", "OpenAI Chat Model"),
    }
    assert expected <= got


def test_good_agent_is_clean():
    agents, findings = mod.audit(load_wf("ai-agent-good.json"))
    assert findings == []
    assert agents[0]["output_parser"] == ["Reply schema"]
    assert agents[0]["memory"] == ["Chat history (Postgres)"]


def test_shared_session_and_missing_model_and_parser():
    nodes = [
        {
            "name": "Agent",
            "type": LC + "agent",
            "parameters": {"hasOutputParser": True, "options": {"systemMessage": "x"}},
        },
        {
            "name": "Memory",
            "type": LC + "memoryPostgresChat",
            "parameters": {"sessionIdType": "customKey", "sessionKey": "support-bot"},
        },
        {
            "name": "lookup_order",
            "type": LC + "toolWorkflow",
            "parameters": {"description": "Look up an order by number and return its status and items."},
        },
    ]
    conns = {**ai("Memory", "ai_memory"), **ai("lookup_order", "ai_tool")}
    got = checks(make_wf(nodes, conns))
    assert ("memory.shared_session", "Memory") in got
    assert ("agent.no_model", "Agent") in got
    assert ("agent.parser_missing", "Agent") in got
    assert not any(n == "lookup_order" for _, n in got)


def test_expression_session_key_is_fine():
    nodes = [
        {"name": "Agent", "type": LC + "agent", "parameters": {"options": {"systemMessage": "x"}}},
        {
            "name": "Mem",
            "type": LC + "memoryBufferWindow",
            "parameters": {"sessionIdType": "customKey", "sessionKey": "={{ $json.chatId }}"},
        },
    ]
    assert ("memory.shared_session", "Mem") not in checks(make_wf(nodes, ai("Mem", "ai_memory")))


def test_no_tools_and_too_many_tools():
    base = [
        {"name": "Agent", "type": LC + "agent", "parameters": {"options": {"systemMessage": "x"}}},
        {"name": "Model", "type": LC + "lmChatOpenAi", "parameters": {"options": {"maxTokens": 500}}},
    ]
    assert ("agent.no_tools", "Agent") in checks(make_wf(base, ai("Model", "ai_languageModel")))
    tools = [
        {
            "name": f"tool_{i}",
            "type": LC + "toolCode",
            "parameters": {"description": "Return the stock level for one SKU from the warehouse."},
        }
        for i in range(4)
    ]
    conns = ai("Model", "ai_languageModel")
    conns["tool_0"] = {"ai_tool": [[{"node": "Agent", "type": "ai_tool", "index": 0}]]}
    for t in tools[1:]:
        conns[t["name"]] = {"ai_tool": [[{"node": "Agent", "type": "ai_tool", "index": 0}]]}
    got = checks(make_wf(base + tools, conns), max_tools=3)
    assert ("agent.too_many_tools", "Agent") in got and ("agent.no_tools", "Agent") not in got


def test_llm_chain_is_audited_without_agent_rules():
    nodes = [
        {"name": "Classify", "type": LC + "chainLlm"},
        {
            "name": "Model",
            "type": LC + "lmChatAnthropic",
            "parameters": {"options": {"maxTokensToSample": 200}},
        },
    ]
    agents, findings = mod.audit(make_wf(nodes, ai("Model", "ai_languageModel", "Classify")))
    assert agents[0]["type"] == "llm-chain" and findings == []


@pytest.mark.parametrize(
    "params,auto_or_desc",
    [
        ({"descriptionType": "auto"}, ("", True)),
        ({"toolDescription": "  Fetch a page  "}, ("Fetch a page", False)),
        ({"description": "Look up"}, ("Look up", False)),
        ({}, ("", False)),
    ],
)
def test_tool_description(params, auto_or_desc):
    assert mod.tool_description({"parameters": params}) == auto_or_desc


@pytest.mark.parametrize(
    "name,typ,default",
    [
        ("HTTP Request", LC + "toolHttpRequest", True),
        ("Call n8n Workflow Tool", LC + "toolWorkflow", True),
        ("Code Tool1", LC + "toolCode", True),
        ("lookup_order", LC + "toolWorkflow", False),
    ],
)
def test_default_tool_names(name, typ, default):
    assert mod.is_default_tool_name({"name": name, "type": typ}) is default


def test_model_name_resource_locator():
    assert mod.model_name({"model": {"__rl": True, "value": "gpt-4o-mini"}}) == "gpt-4o-mini"
    assert mod.model_name({"model": "claude-haiku-4-5"}) == "claude-haiku-4-5"
    assert mod.model_name({}) == ""


def test_cli():
    r = run_script(SKILL, SCRIPT, str(WORKFLOWS), "--json")
    assert r.returncode == 1
    data = json.loads(r.stdout)
    assert {f["workflow"] for f in data["files"]} == {"Support chatbot (prototype)", "Order assistant agent"}
    r = run_script(SKILL, SCRIPT, str(WORKFLOWS / "ai-support-agent-bad.json"))
    assert "AGENT [AI Agent]" in r.stdout
    assert run_script(SKILL, SCRIPT, str(WORKFLOWS / "ai-agent-good.json")).returncode == 0
    r = run_script(SKILL, SCRIPT, str(WORKFLOWS / "order-sync-good.json"))
    assert r.returncode == 0 and "no AI Agent" in r.stdout
    assert run_script(SKILL, SCRIPT, str(WORKFLOWS), "--fail-on", "none").returncode == 0
    assert run_script(SKILL, SCRIPT, "missing.json").returncode == 2
    assert run_script(SKILL, SCRIPT, "--help").returncode == 0
