---
name: n8n-expressions-cheatsheet
description: Write and debug n8n expressions ({{ }} JavaScript with $json, $('Node').item, $input, $now/Luxon, $jmespath, $vars) and Code node data access, with a script that extracts every expression from a workflow JSON and flags common mistakes - {{ }} in fixed (non-expression) fields, unbalanced braces, Python syntax (and/or/True/None), statements instead of one expression, = instead of ===, references to renamed or missing nodes, $json.first-name dot keys, reversed $jmespath arguments, $env (blocked in n8n 2.x), JS Date timezone traps, and Code nodes using $input.item in "Run Once for All Items" mode. Use when an n8n expression shows an error or [undefined], "Referenced node doesn't exist", "Can't get data for expression", paired item errors, date/timezone bugs, or the user asks how to reference data from another node, format dates, or convert JSONata/Make/Zapier formulas to n8n.
license: MIT
compatibility: Python 3.8+ standard library only; reads exported workflow JSON.
metadata:
  author: Saizul Amin
  version: "0.1.0"
---

# n8n expressions cheatsheet

n8n expressions are JavaScript between `{{ }}` in a field set to Expression mode (in JSON
the value starts with `=`). They are evaluated once per item. JSONata is not supported in
expressions; translate JSONata to JavaScript or `$jmespath()`.

Paths below are relative to this skill's folder (in Claude Code: `${CLAUDE_SKILL_DIR}`).

## When to use

- An expression returns `[undefined]`, `[null]`, the literal text `{{ ... }}`, or an error
- "Referenced node doesn't exist", "Can't get data for expression", "Paired item data unavailable"
- How to read data from an earlier node, the first item, all items, or a sub-field with spaces
- Dates off by hours, formatting dates, "yesterday at midnight in my timezone"
- Converting formulas from JSONata, Make, Zapier or Excel into n8n

## When not to use

- The whole workflow needs review: `n8n-workflow-lint`
- Business logic too long for one line: write a Code node (see the reference), or restructure (`n8n-workflow-design`)

## Workflow

1. **Get the failing context**: the node, the field, the expression text, and a sample of
   the input item (one item, redacted). If the user has the workflow JSON, scan it:
   ```bash
   python3 scripts/check_expressions.py wf.json            # findings
   python3 scripts/check_expressions.py wf.json --list     # every expression with node + parameter
   python3 scripts/check_expressions.py workflows/ --json --fail-on high
   ```
2. **Fix findings in order**: high (wrong syntax, missing nodes, `$input.item` in all-items
   Code) first, then medium (literal `{{ }}`, `=` vs `===`, dot keys with `-`), then
   low/info (`$env`, legacy `$node[...]`, JS `Date`).
3. **Diagnose data-shape problems** with this decision list:
   - `[undefined]`: the path is wrong. Open the input panel, switch to JSON view, copy the
     path (drag the field into the expression editor). Keys with spaces or dashes need
     `$json["first name"]`.
   - Works for item 1 only: you used `.first()` or `$('X').first()` where `.item` was meant,
     or the referenced node ran fewer times than the current one.
   - "Paired item data unavailable": an earlier Code/Aggregate node broke item pairing. Use
     `$('X').first()` / `.all()[$itemIndex]`, or carry the field forward with a Set node.
   - Different number of items than expected: every node runs once per input item; use
     "Execute Once" or Aggregate when you need one call.
   - Arrays: `{{ $json.tags.join(', ') }}`; objects into a text field: `{{ JSON.stringify($json.obj) }}`.
4. **Write the corrected expression** using the patterns in
   [references/cheatsheet.md](references/cheatsheet.md). Keep it one expression; use the
   optional chaining `?.` and `??` defaults for fields that may be missing.
5. **Verify**: in the editor, the expression preview under the field shows the result for
   the selected item; check item 1 and a later item. Re-run the script; it should be clean.

## Output

```
Notify Slack > text: fixed value contains {{ }}  -> switch to Expression:
  =New order {{ $json.id }} from {{ $json.email }}
If > condition: Python syntax ("and", "True")
  ={{ $json.total > 100 && $json.paid === true }}
Notify Slack > username: node 'Webhook Trigger' not found (renamed to 'Webhook')
  ={{ $('Webhook').item.json.body.customer }}
Code (all items): uses $input.item -> loop over $input.all() (rewritten code below)
```
