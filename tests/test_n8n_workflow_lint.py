import json

import pytest

from conftest import ROOT, WORKFLOWS, chain, load_script, load_wf, make_wf, run_script

SKILL = "n8n-workflow-lint"
SCRIPT = "lint_workflow.py"
mod = load_script(SKILL, SCRIPT)


def checks(findings, min_sev="info"):
    rank = mod.SEVERITIES.index(min_sev)
    return {(f.check, f.node) for f in findings if mod.SEVERITIES.index(f.severity) >= rank}


def test_bad_workflow_findings():
    found = checks(mod.lint(load_wf("order-sync-bad.json")))
    assert ("credentials.hardcoded", "HTTP Request1") in found
    assert ("credentials.hardcoded", "Send to CRM") in found
    assert ("settings.error_workflow", "-") in found
    assert ("url.internal", "HTTP Request1") in found
    assert ("node.disabled", "Old Mapper") in found
    assert ("node.unused", "Debug") in found
    assert ("pindata.present", "Webhook") in found
    assert ("settings.execution_order", "-") in found
    for name in ("Code", "If", "HTTP Request1", "Webhook"):
        assert ("node.default_name", name) in found
    # the sticky note is never reported
    assert not any(node == "Sticky Note" for _, node in found)


def test_secret_reported_once_per_parameter():
    fs = [f for f in mod.lint(load_wf("order-sync-bad.json")) if f.check == "credentials.hardcoded"]
    assert len([f for f in fs if f.node == "HTTP Request1"]) == 1


def test_secret_values_never_printed():
    r = run_script(SKILL, SCRIPT, str(WORKFLOWS / "order-sync-bad.json"), "--json")
    assert "FAKE-TOKEN-for-tests-only" not in r.stdout
    assert "fake-api-key-value-0000" not in r.stdout
    r = run_script(SKILL, SCRIPT, str(WORKFLOWS / "stripe-webhook-weak.json"))
    assert "whsec_FAKE" not in r.stdout


@pytest.mark.parametrize(
    "name",
    ["order-sync-good.json", "ai-agent-good.json", "error-handler.json"],
)
def test_good_workflows_have_no_low_or_worse(name):
    assert checks(mod.lint(load_wf(name)), "low") == set()


def test_error_handler_asset_is_clean():
    asset = ROOT / "skills" / "n8n-error-handling" / "assets" / "error-handler-workflow.json"
    assert checks(mod.lint(json.loads(asset.read_text())), "low") == set()


def test_ai_subnodes_are_not_unreachable():
    found = checks(mod.lint(load_wf("ai-agent-good.json")))
    assert not any(c in ("node.unreachable", "node.unused") for c, _ in found)


def test_token_patterns_in_code_and_urls():
    gh = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    nodes = [
        {"name": "Start", "type": "n8n-nodes-base.manualTrigger"},
        {"name": "Run code", "type": "n8n-nodes-base.code", "parameters": {"jsCode": f"const t = '{gh}';"}},
        {
            "name": "Call API",
            "type": "n8n-nodes-base.httpRequest",
            "parameters": {"url": "https://bot:hunter22@api.example.com/x"},
        },
        {"name": "Store", "type": "n8n-nodes-base.postgres", "parameters": {"password": "plain-text-pw"}},
    ]
    wf = make_wf(nodes, chain("Start", "Run code", "Call API", "Store"))
    found = checks(mod.lint(wf))
    assert {("credentials.hardcoded", n) for n in ("Run code", "Call API", "Store")} <= found


def test_expression_values_are_not_secrets():
    nodes = [
        {"name": "Start", "type": "n8n-nodes-base.manualTrigger"},
        {
            "name": "Call API",
            "type": "n8n-nodes-base.httpRequest",
            "parameters": {
                "url": "={{ $vars.API }}/x",
                "headerParameters": {"parameters": [{"name": "Authorization", "value": "={{ 'Bearer ' + $json.t }}"}]},
            },
        },
    ]
    assert ("credentials.hardcoded", "Call API") not in checks(mod.lint(make_wf(nodes, chain("Start", "Call API"))))


def test_plain_http_and_dangling_and_duplicates():
    nodes = [
        {"name": "Start", "type": "n8n-nodes-base.manualTrigger"},
        {
            "name": "Fetch",
            "type": "n8n-nodes-base.httpRequest",
            "parameters": {"url": "http://api.example.com/v1"},
        },
        {"name": "Fetch", "type": "n8n-nodes-base.set"},
    ]
    conns = chain("Start", "Fetch")
    conns["Fetch"] = {"main": [[{"node": "Renamed node", "type": "main", "index": 0}]]}
    found = checks(mod.lint(make_wf(nodes, conns)))
    assert ("url.plain_http", "Fetch") in found
    assert ("connections.dangling", "Renamed node") in found
    assert ("node.duplicate_name", "Fetch") in found


def test_unreachable_node():
    nodes = [
        {"name": "Start", "type": "n8n-nodes-base.manualTrigger"},
        {"name": "Step A", "type": "n8n-nodes-base.set"},
        {"name": "Island 1", "type": "n8n-nodes-base.set"},
        {"name": "Island 2", "type": "n8n-nodes-base.set"},
    ]
    conns = {**chain("Start", "Step A"), **chain("Island 1", "Island 2")}
    found = checks(mod.lint(make_wf(nodes, conns)))
    assert ("node.unreachable", "Island 1") in found and ("node.unreachable", "Island 2") in found
    assert ("node.unreachable", "Step A") not in found


def test_long_chain_and_large_workflow_with_loop():
    names = ["Start"] + [f"Step {i}" for i in range(30)]
    nodes = [{"name": "Start", "type": "n8n-nodes-base.manualTrigger"}] + [
        {"name": n, "type": "n8n-nodes-base.set"} for n in names[1:]
    ]
    conns = chain(*names)
    conns["Step 29"] = {"main": [[{"node": "Step 5", "type": "main", "index": 0}]]}  # loop back
    wf = make_wf(nodes, conns)
    found = checks(mod.lint(wf, max_chain=20, max_nodes=25))
    assert ("chain.long", "-") in found and ("workflow.large", "-") in found
    assert mod.longest_chain(set(names), mod.edges(wf)) == 31
    assert ("chain.long", "-") not in checks(mod.lint(wf, max_chain=40, max_nodes=100))


@pytest.mark.parametrize(
    "name,typ,default",
    [
        ("HTTP Request1", "n8n-nodes-base.httpRequest", True),
        ("Edit Fields", "n8n-nodes-base.set", True),
        ("Loop Over Items", "n8n-nodes-base.splitInBatches", True),
        ("AI Agent", "@n8n/n8n-nodes-langchain.agent", True),
        ("Schedule Trigger", "n8n-nodes-base.scheduleTrigger", True),
        ("Fetch invoices", "n8n-nodes-base.httpRequest", False),
        ("Code in Python", "n8n-nodes-base.code", False),
    ],
)
def test_default_names(name, typ, default):
    assert mod.is_default_name({"name": name, "type": typ}) is default


def test_manual_only_workflow_error_workflow_is_info():
    wf = make_wf([{"name": "Run backfill", "type": "n8n-nodes-base.manualTrigger"}])
    f = [x for x in mod.lint(wf) if x.check == "settings.error_workflow"]
    assert f and f[0].severity == "info"


def test_load_formats(tmp_path):
    wf = load_wf("order-sync-good.json")
    (tmp_path / "api.json").write_text(json.dumps({"data": [wf, wf]}))
    assert len(mod.load_workflows([str(tmp_path / "api.json")])) == 2
    assert len(mod.load_workflows([str(WORKFLOWS / "export-all.json")])) == 2
    assert len(mod.load_workflows([str(WORKFLOWS)])) == 8
    (tmp_path / "x.json").write_text('{"hello": 1}')
    with pytest.raises(ValueError):
        mod.load_workflows([str(tmp_path / "x.json")])


def test_cli(tmp_path):
    bad = str(WORKFLOWS / "order-sync-bad.json")
    good = str(WORKFLOWS / "order-sync-good.json")
    assert run_script(SKILL, SCRIPT, bad).returncode == 1
    assert run_script(SKILL, SCRIPT, good).returncode == 0
    assert run_script(SKILL, SCRIPT, bad, "--fail-on", "none").returncode == 0
    r = run_script(SKILL, SCRIPT, bad, "--json", "--ignore", "credentials", "--min-severity", "medium")
    data = json.loads(r.stdout)
    checks_seen = {f["check"] for f in data["files"][0]["findings"]}
    assert "credentials.hardcoded" not in checks_seen and "node.unused" not in checks_seen
    assert data["summary"]["high"] == 0
    r = run_script(SKILL, SCRIPT, "-", stdin=(WORKFLOWS / "order-sync-good.json").read_text())
    assert r.returncode == 0 and "0 finding(s)" in r.stdout
    assert run_script(SKILL, SCRIPT, str(tmp_path / "missing.json")).returncode == 2
    (tmp_path / "broken.json").write_text("{nope")
    assert run_script(SKILL, SCRIPT, str(tmp_path / "broken.json")).returncode == 2
    assert run_script(SKILL, SCRIPT, "--help").returncode == 0
