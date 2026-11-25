---
name: n8n-error-handling
description: Make n8n failures visible and recoverable - an Error Trigger alert workflow (ready-to-import JSON included), Retry On Fail settings, node "On Error" modes (stop, continue, error output), dead-letter tables and replay, Stop And Error for business rules, and alert noise control. Includes a script that audits a whole export for workflows without an error workflow, error workflows that have no Error Trigger or notify nobody, swallowed errors (continueOnFail), unconnected error outputs, external calls without retries and non-idempotent retried POSTs. Use when workflows fail silently, the user asks for alerts/notifications on n8n errors, retries, "what happens if this API is down", dead letter queues, or replaying failed items.
license: MIT
compatibility: Python 3.8+ standard library only; reads exported workflow JSON.
metadata:
  author: Saizul Amin
  version: "0.1.0"
---

# n8n error handling

Goal: every production failure produces an alert with a link to the execution, transient
errors retry by themselves, and per-item failures land somewhere they can be replayed.

Paths below are relative to this skill's folder (in Claude Code: `${CLAUDE_SKILL_DIR}`).

## When to use

- "My workflow failed and nobody noticed", "send me a Slack message when n8n fails"
- Retries for flaky APIs, 429/5xx handling, timeouts
- "If one item fails the whole run stops" / "failed items disappear"
- Designing dead-letter storage and a replay path
- Auditing many workflows for error-handling coverage

## When not to use

- Designing the happy path or idempotency: `n8n-workflow-design`
- Workers crashing, queue stuck, executions not starting: `n8n-self-hosting`
- Expression errors ("Cannot read properties of undefined"): `n8n-expressions-cheatsheet`

## Workflow

1. **Audit what exists** (read-only). Export all workflows so references resolve:
   ```bash
   n8n export:workflow --all --separate --output=workflows/     # on the server / in the container
   python3 scripts/error_audit.py workflows/
   python3 scripts/error_audit.py workflows/ --json --fail-on high
   ```
   The report ends with "Error workflow coverage": which alert workflow each workflow uses.
2. **Create one alert workflow** if none exists: import
   [assets/error-handler-workflow.json](assets/error-handler-workflow.json)
   (Workflows > Import from file), replace the incoming-webhook URL or swap the HTTP node
   for Slack/Email/Telegram/Teams with a credential, save it, and make sure it is
   published. Error Trigger fields are in
   [references/patterns.md](references/patterns.md#error-trigger-data).
3. **Attach it.** In every production workflow: Settings > Error workflow > the alert
   workflow. Ask before editing many workflows; list them first. Sub-workflows called
   with "wait for completion" report failures through the parent.
4. **Choose behaviour per node** (decision table in
   [references/patterns.md](references/patterns.md#choosing-on-error)):
   - transient (network, 429, 5xx): Retry On Fail, 3 tries, 2000-5000 ms
   - per-item, expected failures (bad email, 404): On Error > Continue (using error
     output) -> dead-letter branch
   - must-not-continue: keep Stop Workflow (default) so the error workflow fires
   - business rule broken (amount negative): Stop And Error node with a clear message
5. **Add a dead-letter branch** where items can fail individually: write the failed item,
   the error message, workflow/execution id and timestamp to a table, and build a small
   replay workflow (SQL and layout in
   [references/patterns.md](references/patterns.md#dead-letter-and-replay)).
6. **Control noise**: one alert per execution (the error workflow already is), group
   repeated alerts (dedupe key `workflow.id + error.message` with a short TTL), and send
   warnings (dead-letter rows) as a daily digest instead of pages.
7. **Test it**: temporarily point a node at a bad host in a copy of the workflow, publish
   it, trigger it in production mode (Error Trigger does not fire for manual test runs),
   confirm the alert arrives, then revert.
8. **Re-run the audit** and report what changed.

## Output

```
Error handling audit (14 workflows)
- 9 production workflows had no error workflow -> set to "Alerts: workflow failed" (list)
- "Nightly sales report" pointed to a deleted workflow (wf-does-not-exist) -> fixed
- Stripe: invoice paid / Mark invoice paid: error output not connected -> dead_letter insert
- 6 external calls without retries -> Retry On Fail 3x/2000 ms (POST to /charges skipped:
  not idempotent, added Idempotency-Key first)
Test: forced failure in "Shopify: order created (copy)" -> alert received in #alerts
```
