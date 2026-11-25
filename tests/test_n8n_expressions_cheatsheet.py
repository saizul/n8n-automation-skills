import json

import pytest

from conftest import WORKFLOWS, load_script, load_wf, make_wf, run_script

SKILL = "n8n-expressions-cheatsheet"
SCRIPT = "check_expressions.py"
mod = load_script(SKILL, SCRIPT)
NAMES = {"Webhook", "Fetch order", "My Node"}


def js_checks(js):
    return {c for c, *_ in mod.check_js(js, NAMES)}


def test_bad_workflow():
    exprs, findings = mod.collect(load_wf("order-sync-bad.json"))
    got = {(f.check, f.node) for f in findings}
    assert ("expr.missing_equals", "Notify Slack") in got
    assert ("expr.missing_node", "Notify Slack") in got
    assert ("expr.invalid_dot_key", "Notify Slack") in got
    assert ("expr.python_syntax", "If") in got
    assert ("expr.legacy_syntax", "Old Mapper") in got
    assert ("code.item_in_all_mode", "Code") in got
    assert len(exprs) == 6
    # sticky note content with {{ }} is ignored
    assert not any(f.node == "Sticky Note" for f in findings)


@pytest.mark.parametrize(
    "name", ["order-sync-good.json", "ai-agent-good.json", "error-handler.json", "export-all.json"]
)
def test_good_workflows_clean(name):
    from conftest import WORKFLOWS as W

    for _, wf in mod.load_workflows([str(W / name)]):
        _, findings = mod.collect(wf)
        assert [f for f in findings if f.severity not in ("info",)] == []


@pytest.mark.parametrize(
    "js,expected",
    [
        (" $json.a and $json.b ", "expr.python_syntax"),
        (" $json.x == None ", "expr.python_syntax"),
        (" not $json.ok ", "expr.python_syntax"),
        (" const a = 1; a + 1 ", "expr.statement"),
        (" return $json.a ", "expr.statement"),
        (" $json.status = 'paid' ", "expr.assignment"),
        (" $('Old name').item.json.id ", "expr.missing_node"),
        (' $node["Gone"].json.id ', "expr.missing_node"),
        (" $node.Gone.json ", "expr.missing_node"),
        (' $items("Gone") ', "expr.missing_node"),
        (' $node["Webhook"].json.body ', "expr.legacy_syntax"),
        (" $json.first-name ", "expr.invalid_dot_key"),
        (" $json.body.x-request-id ", "expr.invalid_dot_key"),
        (" $jmespath('items[0]', $json) ", "expr.jmespath_args"),
        (" $env.API_KEY ", "expr.env_access"),
        (" new Date().toISOString() ", "expr.js_date"),
        ("   ", "expr.empty"),
    ],
)
def test_check_js_detects(js, expected):
    assert expected in js_checks(js)


@pytest.mark.parametrize(
    "js",
    [
        " $json.total > 100 && $json.paid === true ",
        " $('Fetch order').item.json.id ",
        " $('Webhook').first().json.body ",
        " $json['first-name'] ",
        " $json.total - $json.discount ",
        " $json.items.map(i => i.sku).join(', ') ",
        " (() => { const t = $json.t; return t * 2; })() ",
        " $json.note === 'and or not True' ",
        " $jmespath($json, 'items[0]') ",
        " $now.minus({ days: 1 }).toISODate() ",
        " $json.a >= 1 && $json.b <= 2 && $json.c !== 3 ",
        " $json.customer?.city ?? 'unknown' ",
        " $('My Node').all().length ",
    ],
)
def test_check_js_clean(js):
    assert js_checks(js) == set()


@pytest.mark.parametrize(
    "expr,segs,balanced",
    [
        ("{{ $json.a }}", [" $json.a "], True),
        ("Hi {{ $json.a }} and {{ $json.b }}", [" $json.a ", " $json.b "], True),
        ("{{ $json.a ", [], False),
        ("$json.a }}", [], False),
        ("{{ {a: 1}.a }}", [" {a: 1}.a "], True),
        ("{{ JSON.stringify({ text: $json.t }) }}", [" JSON.stringify({ text: $json.t }) "], True),
        ("no braces", [], True),
    ],
)
def test_segments(expr, segs, balanced):
    assert mod.segments(expr) == (segs, balanced)


def test_unbalanced_and_missing_equals_in_workflow():
    nodes = [
        {"name": "Start", "type": "n8n-nodes-base.manualTrigger"},
        {
            "name": "Set fields",
            "type": "n8n-nodes-base.set",
            "parameters": {
                "a": "={{ $json.a ",
                "b": "plain {{ $json.b }}",
                "c": "plain text",
                "d": "=plain text",
            },
        },
    ]
    _, findings = mod.collect(make_wf(nodes))
    got = {(f.check, f.param) for f in findings}
    assert ("expr.unbalanced", "a") in got and ("expr.missing_equals", "b") in got
    assert not any(p in ("c", "d") for _, p in got)


@pytest.mark.parametrize(
    "mode,code,expected",
    [
        ("runOnceForAllItems", "return [{ json: { id: $input.item.json.id } }];", "code.item_in_all_mode"),
        (None, "return [{ json: { id: $json.id } }];", "code.item_in_all_mode"),
        (
            "runOnceForEachItem",
            "const all = $input.all(); return { json: { n: all.length } };",
            "code.all_in_item_mode",
        ),
        ("runOnceForAllItems", "return [{ json: { id: '{{ $json.id }}' } }];", "code.braces"),
        ("runOnceForAllItems", "const x = `${$json.id}`; return [{ json: { x } }];", None),
        ("runOnceForAllItems", "return $input.all().map(i => ({ json: { id: i.json.id } }));", None),
        (
            "runOnceForAllItems",
            "const c = require('crypto'); const _ = require('lodash'); return [];",
            "code.require",
        ),
        ("runOnceForAllItems", "const k = $env.SECRET; return [];", "expr.env_access"),
    ],
)
def test_code_checks(mode, code, expected):
    params = {"jsCode": code}
    if mode:
        params["mode"] = mode
    got = {c for _, c, *_ in mod.check_code({"type": "n8n-nodes-base.code", "parameters": params})}
    if expected:
        assert expected in got
    else:
        assert got == set()


def test_require_fix_lists_builtin_and_external():
    res = mod.check_code(
        {"type": "n8n-nodes-base.code", "parameters": {"jsCode": "require('crypto'); require('lodash');"}}
    )
    fix = [r for r in res if r[1] == "code.require"][0][4]
    assert "NODE_FUNCTION_ALLOW_BUILTIN=crypto" in fix and "NODE_FUNCTION_ALLOW_EXTERNAL=lodash" in fix


def test_code_params_are_not_scanned_as_expressions():
    nodes = [{"name": "Code", "type": "n8n-nodes-base.code", "parameters": {"jsCode": "x = '{{ $json.a }}'"}}]
    _, findings = mod.collect(make_wf(nodes))
    assert not any(f.check == "expr.missing_equals" for f in findings)


def test_cli():
    bad = str(WORKFLOWS / "order-sync-bad.json")
    r = run_script(SKILL, SCRIPT, bad, "--list")
    assert r.returncode == 1 and "EXPR [Notify Slack]" in r.stdout
    r = run_script(SKILL, SCRIPT, bad, "--json", "--list")
    data = json.loads(r.stdout)
    assert data["files"][0]["expression_count"] == 6 and data["files"][0]["expressions"]
    assert run_script(SKILL, SCRIPT, bad, "--fail-on", "none").returncode == 0
    assert run_script(SKILL, SCRIPT, str(WORKFLOWS / "order-sync-good.json")).returncode == 0
    assert run_script(SKILL, SCRIPT, str(WORKFLOWS / "order-sync-good.json"), "--fail-on", "info").returncode == 1
    assert run_script(SKILL, SCRIPT, "does-not-exist.json").returncode == 2
    assert run_script(SKILL, SCRIPT, "--help").returncode == 0
