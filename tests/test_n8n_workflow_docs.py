import json
import re

from conftest import WORKFLOWS, load_script, load_wf, make_wf, run_script

SKILL = "n8n-workflow-docs"
SCRIPT = "workflow_docs.py"
mod = load_script(SKILL, SCRIPT)


def test_render_bad_workflow():
    info = mod.collect(load_wf("order-sync-bad.json"))
    md = mod.render(info, "order-sync-bad.json")
    assert md.startswith("# Order sync (legacy)\n")
    assert "| Error workflow | **none** |" in md
    assert "POST `/webhook/orders`, auth: none" in md
    assert "```mermaid\nflowchart LR" in md
    assert "-->|true|" in md and "-->|false|" in md
    assert ":::disabled" in md
    assert "`slackApi`: Slack bot (used by Notify Slack)" in md
    assert "- 10.0.0.12" in md and "- crm.example.com" in md
    assert "> ## Order sync" in md  # sticky note content
    # node order follows the flow from the trigger; orphan last
    names = [n["name"] for n in info["nodes"]]
    assert names[0] == "Webhook" and names[-1] == "Debug"


def test_secrets_are_not_rendered():
    md = mod.render(mod.collect(load_wf("order-sync-bad.json")), "x.json")
    assert "FAKE-TOKEN" not in md and "fake-api-key" not in md
    md = mod.render(mod.collect(load_wf("stripe-webhook-weak.json")), "x.json")
    assert "whsec_" not in md


def test_loop_error_output_and_ai_edges():
    batch = mod.load_workflows([str(WORKFLOWS / "export-all.json")])[1][1]
    m = mod.mermaid(batch)
    assert "-->|loop|" in m and "-->|error|" in m
    agent = mod.mermaid(load_wf("ai-agent-good.json"))
    for label in ("model", "memory", "tool", "parser"):
        assert f"-.->|{label}|" in agent


def test_schedule_and_subworkflows():
    nightly = mod.load_workflows([str(WORKFLOWS / "export-all.json")])[0][1]
    info = mod.collect(nightly)
    assert info["triggers"][0]["detail"] == "every 1 days at 06:00"
    assert info["timezone"] == "Asia/Dhaka"
    good = mod.collect(load_wf("order-sync-good.json"))
    assert {"node": "Upsert order in CRM", "name": "CRM: upsert order", "id": "wf-crm-upsert"} in good["sub_workflows"]
    cron = make_wf(
        [
            {
                "name": "Cron",
                "type": "n8n-nodes-base.scheduleTrigger",
                "parameters": {"rule": {"interval": [{"field": "cronExpression", "expression": "0 */2 * * *"}]}},
            }
        ]
    )
    assert mod.collect(cron)["triggers"][0]["detail"] == "cron `0 */2 * * *`"


def test_mermaid_escaping():
    wf = make_wf(
        [
            {"name": 'Say "hi" <b>', "type": "n8n-nodes-base.manualTrigger"},
            {"name": "Is it?", "type": "n8n-nodes-base.if"},
        ],
        {'Say "hi" <b>': {"main": [[{"node": "Is it?", "type": "main", "index": 0}]]}},
    )
    m = mod.mermaid(wf)
    assert "#quot;hi#quot; #lt;b#gt;" in m
    assert re.search(r'n1\{"Is it\?', m)  # decision shape
    assert "n0 --> n1" in m


def test_out_directory(tmp_path):
    out = tmp_path / "docs"
    r = run_script(SKILL, SCRIPT, str(WORKFLOWS), "--out", str(out))
    assert r.returncode == 0
    files = sorted(p.name for p in out.iterdir())
    assert "README.md" in files and "shopify-order-created.md" in files and len(files) == 9
    index = (out / "README.md").read_text()
    assert "[Nightly sales report](nightly-sales-report.md)" in index


def test_cli_modes():
    r = run_script(SKILL, SCRIPT, str(WORKFLOWS / "error-handler.json"), "--mermaid-only")
    assert r.returncode == 0 and r.stdout.startswith("%% Error handler: Slack alert\nflowchart LR")
    r = run_script(SKILL, SCRIPT, str(WORKFLOWS / "export-all.json"), "--json")
    data = json.loads(r.stdout)
    assert [d["name"] for d in data] == ["Nightly sales report", "Contacts: batch import"]
    r = run_script(SKILL, SCRIPT, "-", stdin=(WORKFLOWS / "order-sync-good.json").read_text())
    assert r.returncode == 0 and "# Shopify: order created" in r.stdout
    assert run_script(SKILL, SCRIPT, "missing.json").returncode == 2
    assert run_script(SKILL, SCRIPT, "--help").returncode == 0
