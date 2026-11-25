import json

from conftest import ROOT, WORKFLOWS, chain, load_script, load_wf, make_wf, run_script

SKILL = "n8n-error-handling"
SCRIPT = "error_audit.py"
mod = load_script(SKILL, SCRIPT)
ASSET = ROOT / "skills" / SKILL / "assets" / "error-handler-workflow.json"


def all_by_id():
    return {str(w["id"]): w for _, w in mod.load_workflows([str(WORKFLOWS)]) if w.get("id")}


def checks(wf, by_id=None):
    return {(f.check, f.node) for f in mod.audit(wf, all_by_id() if by_id is None else by_id)}


def test_bad_workflow():
    got = checks(load_wf("order-sync-bad.json"))
    assert ("error.no_error_workflow", "-") in got
    assert {("error.no_retry", n) for n in ("HTTP Request1", "Send to CRM", "Notify Slack")} <= got


def test_unknown_error_workflow_and_good_ones():
    nightly, batch = [w for _, w in mod.load_workflows([str(WORKFLOWS / "export-all.json")])]
    assert ("error.unknown_error_workflow", "-") in checks(nightly)
    assert not any(c.startswith("error.unknown") for c, _ in checks(batch))
    # the batch import connects its error output to a dead-letter insert
    assert not any(c == "error.error_output_unconnected" for c, _ in checks(batch))
    assert checks(load_wf("order-sync-good.json")) == set()
    assert checks(load_wf("error-handler.json")) == set()


def test_unknown_reference_not_reported_without_context():
    nightly = mod.load_workflows([str(WORKFLOWS / "export-all.json")])[0][1]
    assert not any(c.startswith("error.unknown") for c, _ in checks(nightly, by_id={}))


def test_error_output_unconnected():
    got = checks(load_wf("stripe-webhook-weak.json"))
    assert ("error.error_output_unconnected", "Mark invoice paid") in got


def test_target_without_error_trigger_and_self_reference():
    target = make_wf([{"name": "Start", "type": "n8n-nodes-base.manualTrigger"}], id="wf-not-handler")
    user = make_wf(
        [{"name": "Every hour", "type": "n8n-nodes-base.scheduleTrigger"}],
        id="wf-user",
        settings={"errorWorkflow": "wf-not-handler"},
    )
    assert ("error.target_not_error_trigger", "-") in checks(user, {"wf-not-handler": target, "wf-user": user})
    selfref = make_wf(
        [{"name": "Every hour", "type": "n8n-nodes-base.scheduleTrigger"}],
        id="wf-self",
        settings={"errorWorkflow": "wf-self"},
    )
    assert ("error.self_reference", "-") in checks(selfref, {"wf-self": selfref})


def test_handler_without_notification():
    wf = make_wf(
        [
            {"name": "On error", "type": "n8n-nodes-base.errorTrigger"},
            {"name": "Shape", "type": "n8n-nodes-base.set"},
        ],
        chain("On error", "Shape"),
    )
    assert ("error.handler_no_notify", "On error") in checks(wf, {})


def test_swallowed_and_retry_idempotency_and_failed_not_saved():
    nodes = [
        {"name": "Hook", "type": "n8n-nodes-base.webhook"},
        {
            "name": "Charge card",
            "type": "n8n-nodes-base.httpRequest",
            "parameters": {"method": "POST", "url": "https://api.example.com/charges"},
            "retryOnFail": True,
        },
        {
            "name": "Create invoice",
            "type": "n8n-nodes-base.httpRequest",
            "parameters": {
                "method": "POST",
                "url": "https://api.example.com/invoices",
                "headerParameters": {"parameters": [{"name": "Idempotency-Key", "value": "={{ $json.id }}"}]},
            },
            "retryOnFail": True,
            "continueOnFail": True,
        },
        {"name": "Legacy mode", "type": "n8n-nodes-base.set", "onError": "continueRegularOutput"},
        {"name": "Ignored", "type": "n8n-nodes-base.httpRequest", "disabled": True},
    ]
    wf = make_wf(
        nodes,
        chain("Hook", "Charge card", "Create invoice", "Legacy mode"),
        settings={"errorWorkflow": "x", "saveDataErrorExecution": "none"},
    )
    got = checks(wf, {})
    assert ("error.retry_not_idempotent", "Charge card") in got
    assert ("error.retry_not_idempotent", "Create invoice") not in got
    assert ("error.swallowed", "Create invoice") in got and ("error.swallowed", "Legacy mode") in got
    assert ("error.failed_not_saved", "-") in got
    assert not any(n == "Ignored" for _, n in got)


def test_if_node_error_output_index():
    nodes = [
        {"name": "Start", "type": "n8n-nodes-base.manualTrigger"},
        {"name": "Check", "type": "n8n-nodes-base.if", "onError": "continueErrorOutput"},
        {"name": "Yes", "type": "n8n-nodes-base.noOp"},
        {"name": "No", "type": "n8n-nodes-base.noOp"},
        {"name": "Oops", "type": "n8n-nodes-base.noOp"},
    ]
    conns = chain("Start", "Check")
    conns["Check"] = {
        "main": [
            [{"node": "Yes", "type": "main", "index": 0}],
            [{"node": "No", "type": "main", "index": 0}],
        ]
    }
    assert ("error.error_output_unconnected", "Check") in checks(make_wf(nodes, conns), {})
    conns["Check"]["main"].append([{"node": "Oops", "type": "main", "index": 0}])
    assert ("error.error_output_unconnected", "Check") not in checks(make_wf(nodes, conns), {})


def test_asset_is_a_valid_error_workflow():
    wf = json.loads(ASSET.read_text())
    assert any(n["type"] == "n8n-nodes-base.errorTrigger" for n in wf["nodes"])
    assert {c for c, _ in checks(wf, {})} <= {"error.retry_not_idempotent"}


def test_cli():
    r = run_script(SKILL, SCRIPT, str(WORKFLOWS), "--json")
    assert r.returncode == 1
    data = json.loads(r.stdout)
    assert "Error handler: Slack alert" in data["error_workflows"]
    assert "<missing wf-does-not-exist>" in data["error_workflows"]
    r = run_script(SKILL, SCRIPT, str(WORKFLOWS))
    assert "Error workflow coverage:" in r.stdout
    assert run_script(SKILL, SCRIPT, str(WORKFLOWS / "order-sync-good.json")).returncode == 0
    assert run_script(SKILL, SCRIPT, str(WORKFLOWS), "--fail-on", "high").returncode == 0
    assert run_script(SKILL, SCRIPT, str(WORKFLOWS), "--fail-on", "none").returncode == 0
    assert run_script(SKILL, SCRIPT, "nope.json").returncode == 2
    assert run_script(SKILL, SCRIPT, "--help").returncode == 0
