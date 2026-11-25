---
name: n8n-workflow-design
description: Design n8n workflows that survive production - choosing the trigger (Webhook, Schedule, app trigger, polling, Execute Workflow), splitting work into sub-workflows, making runs idempotent so retries and duplicate webhooks do not double-charge or double-email, batching and pagination with Loop Over Items, rate limits, long waits, and item/paired-item data flow. Produces a node-by-node plan with names, settings and failure behaviour. Use when the user wants to build or restructure an n8n workflow or automation, asks "how should I structure this in n8n", hits duplicate executions, timeouts, memory errors on big datasets, 429 rate limits, or a workflow that has grown too large to follow.
license: MIT
metadata:
  author: Saizul Amin
  version: "0.1.0"
---

# n8n workflow design

Turn a business process into an n8n design that is idempotent, observable and easy to
change. Design on paper first; build in the editor only after the user agrees on the plan.

Paths below are relative to this skill's folder (in Claude Code: `${CLAUDE_SKILL_DIR}`).

## When to use

- "Build an n8n workflow that syncs Shopify orders to HubSpot / Sheets / Postgres"
- "Should this be one workflow or several?", "my workflow has 80 nodes"
- Duplicate records or emails after retries or repeated webhooks
- Timeouts, "JavaScript heap out of memory", or 429 Too Many Requests on large runs
- Choosing between a Schedule Trigger, polling app trigger and a webhook

## When not to use

- Reviewing an existing export for mistakes: run `n8n-workflow-lint` first, then come back
- Failure alerting and retries in depth: `n8n-error-handling`
- Securing a public webhook: `n8n-webhook-security`
- AI agent specifics (tools, memory, cost): `n8n-ai-agent-workflows`
- Installing or scaling n8n itself: `n8n-self-hosting`

## Workflow

1. **Capture the process.** Ask for (or infer and confirm):
   - the event that starts it and its volume (per minute / per day, peak burst)
   - source and target systems, which side is the source of truth
   - what "done" means for one record and what must never happen twice
     (charge, email, ticket, invoice)
   - latency needed (seconds vs hourly batch) and acceptable data loss (none?)
2. **Pick the trigger** with the table in [references/patterns.md](references/patterns.md#triggers).
   Prefer a push webhook when the source offers signed webhooks; add a scheduled
   reconciliation run when missing an event is unacceptable (webhooks get lost).
3. **Cut it into workflows.** One workflow per trigger. Move reusable or heavy parts into
   sub-workflows started with Execute Workflow (Execute Workflow Trigger with a defined
   input schema). Split when a workflow passes ~30 nodes, when two triggers share logic,
   or when a step must run with different retries/concurrency. See
   [references/patterns.md](references/patterns.md#sub-workflows).
4. **Make every write idempotent.** For each node that creates or sends something, choose:
   - upsert by a natural key (order id, email) instead of create
   - an idempotency key header (`Idempotency-Key: {{ $json.order_id }}`) when the API supports it
   - a "seen" table (Postgres / Data table) checked before acting, written after success
   Write the dedupe key down in the plan. Details and SQL in
   [references/patterns.md](references/patterns.md#idempotency).
5. **Size the data path.** If a run can see more than ~500 items or large binaries:
   paginate at the source (HTTP Request pagination options), process in batches with
   Loop Over Items (batch 20-200), pass only ids to sub-workflows and fetch details inside,
   and keep binaries out of memory (`N8N_DEFAULT_BINARY_DATA_MODE` filesystem/database/s3).
   Respect rate limits with batch size + Wait, and Retry On Fail for 429/5xx.
6. **Decide failure behaviour per node**: stop (default), retry (transient APIs),
   continue via error output to a dead-letter branch (per-item failures), or Stop And
   Error (business rule violated). Every production workflow gets an error workflow
   (`n8n-error-handling`).
7. **Write the plan** in the Output format below, including node names (verbs, not
   "HTTP Request1"), credentials needed and test data. Get the user's OK.
8. **Build and verify.** Build in the editor or as JSON; pin realistic test data while
   building and unpin before activating. Then run
   `python3 ../n8n-workflow-lint/scripts/lint_workflow.py workflow.json` if that skill is
   installed, and fix high/medium findings.

## Design rules of thumb

- Name nodes by what they do: "Fetch unpaid invoices", "Upsert contact in HubSpot".
- Keep data flat and small: a Set (Edit Fields) node after each API call keeping only
  needed fields reduces memory and makes expressions short.
- Reference earlier nodes with `$('Node name').item.json` (paired item) or `.first()`
  for single-value lookups; avoid long chains of `$json` that break when a node is inserted.
- Do not put secrets or environment-specific URLs in parameters: credentials and `$vars`.
- Webhooks that do real work should respond fast (Respond to Webhook early, 202) and
  continue processing, or hand off to a queue-backed sub-workflow.
- Long waits (hours/days) use the Wait node; executions resume from the database, so they
  survive restarts. Do not use loops with short waits as a scheduler.

## Output

```
Plan: Shopify order -> HubSpot deal (+ nightly reconciliation)

Workflows
1. "Shopify: order created" (Webhook, raw body, HMAC verified)  ~200/day, burst 20/min
   Receive order -> Verify signature -> Respond 202 -> Execute "CRM: upsert order"
2. "CRM: upsert order" (Execute Workflow Trigger, input {order_id})
   Already processed? (Postgres processed_orders) -> Fetch order -> Upsert deal (key: order_id)
   -> Mark processed
3. "Reconcile orders" (Schedule 02:00) - list orders updated in the last 26h, run #2 for each

Idempotency: deal upsert keyed by shopify_order_id; processed_orders(order_id PK)
Failures: Fetch/Upsert retry 3x 2s; per-item errors -> dead_letter table; error workflow "Alerts"
Credentials: Shopify (webhook secret + admin token), HubSpot app token, Postgres
Open questions: refunds/cancellations in scope?
```
