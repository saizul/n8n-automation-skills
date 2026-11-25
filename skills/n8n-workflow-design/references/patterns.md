# n8n design patterns

## Triggers

| Trigger | Use when | Watch out for |
| --- | --- | --- |
| Webhook | Source pushes events (Stripe, Shopify, GitHub, forms) | Verify signatures (raw body), respond fast, events can be lost or duplicated: dedupe and reconcile |
| App trigger (e.g. "Gmail Trigger", "Postgres Trigger") | n8n node exists and polling/listening is enough | Polling triggers check every N minutes and keep state in the workflow's static data; reactivating can re-read items |
| Schedule Trigger | Batch jobs, reconciliation, reports | Uses `GENERIC_TIMEZONE` unless the workflow sets a timezone; overlapping runs if a run takes longer than the interval |
| Execute Workflow Trigger | Sub-workflows, shared logic, agent tools | Define the input fields so callers and the editor know the contract |
| Error Trigger | The alert workflow other workflows point to | Only runs for production executions, not manual tests |
| Form Trigger / Chat Trigger | Human input | Public by default once published: see `n8n-webhook-security` |
| Manual Trigger | One-off scripts, backfills | Never the only trigger of something that must run in production |

Prevent overlapping scheduled runs: at the start, check a lock row
(`INSERT ... ON CONFLICT DO NOTHING RETURNING`) and stop if no row is returned; delete it
at the end and in the error path.

## Sub-workflows

Split a workflow when:

- it passes ~30 nodes, or the canvas no longer fits on one screen
- two triggers (webhook + nightly reconcile) share the same processing
- one step needs different concurrency or retries (e.g. a slow API)
- the same logic is used as an AI agent tool

Contract:

- Execute Workflow Trigger with "Define using fields below" (or a JSON example) listing
  every input: `order_id` (string), `source` (string).
- The last node's output is what the parent receives. End with a Set node returning a
  small, documented result (`{ status, id }`).
- Pass ids, not full payloads, so the sub-workflow can be re-run by hand from an id.
- "Wait for sub-workflow completion" off = fire and forget (parent does not see errors;
  the child needs its own error workflow).
- Since n8n 2.0, when a child workflow waits (Wait node) and resumes, the parent receives
  the child's output, not its input; re-test parents that relied on the old behaviour.

## Idempotency

A retry, a re-delivered webhook or a manual re-run must not repeat side effects.

1. **Natural-key upsert**: most CRMs and databases support upsert (HubSpot contact by
   email, Postgres `INSERT ... ON CONFLICT (order_id) DO UPDATE`).
2. **Idempotency key**: Stripe and many payment/email APIs accept an `Idempotency-Key`
   header. Use a stable value from the event (`{{ $json.event_id }}`), not `$execution.id`
   (which changes when you retry the execution from the UI).
3. **Processed-events table**:

```sql
CREATE TABLE IF NOT EXISTS processed_events (
  source      text        NOT NULL,
  event_id    text        NOT NULL,
  processed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (source, event_id)
);
-- First node after the trigger (Postgres, Execute Query):
INSERT INTO processed_events (source, event_id) VALUES ('shopify', $1)
ON CONFLICT DO NOTHING RETURNING event_id;
```

   If the query returns no row, the event was already handled: stop (IF on
   `{{ $json.event_id }}` exists). If later steps fail, delete the row in the error
   branch so a retry can run, or insert the row only after the side effect succeeded and
   accept a small duplicate window.

4. **Webhook duplicates**: providers retry on timeouts. Respond within a few seconds
   (Respond to Webhook early) so they do not retry while you are still working.

## Batching and pagination

- **Paginate at the source**: HTTP Request > Options > Pagination (next URL from response,
  or page parameter with "complete when" condition). Set a max pages limit.
- **Loop Over Items** (Split in Batches v3): output `loop` gets each batch, `done` fires
  once at the end. Connect the last node of the batch back to Loop Over Items.
- Batch size: 20-200 items; smaller when each item does several API calls.
- **Rate limits**: batch + Wait (e.g. 50 per batch, wait 1 s = max 50 req/s); many nodes
  also have "Batching" options under HTTP Request > Options > Batching.
- **Big datasets**: run a parent that fetches ids and a sub-workflow per batch, so each
  execution's memory is freed. Avoid Code nodes that build huge arrays.
- **Memory**: the "Aggregate", "Merge" and "Sort" nodes hold everything in memory.

## Data flow

- Each node runs once per item unless "Execute Once" is set. Ten items in, ten API calls.
- Paired items: `$('Fetch order').item` returns the item that produced the current one.
  It breaks after nodes that change item counts without pairing (some Code nodes,
  Aggregate). Then use `.first()`, `.all()` or carry the needed fields along with a Set node.
- Merge node modes: "Combine by matching fields" (join), "Combine by position",
  "Append". Choose explicitly; the default is append.
- IF/Switch: items that match no rule are dropped unless you connect the false/fallback output.

## Long waits and human steps

- Wait node: "After time interval", "At specified time", "On webhook call" (resume URL in
  `$execution.resumeUrl`) and "On form submitted". Waiting executions are stored in the
  database and survive restarts.
- "Send and wait for response" operations (Slack, email, Telegram, etc.) implement
  approvals without building your own resume webhook.

## Environments

- Keep one n8n per environment (dev/staging/prod). Move workflows with source control
  (Enterprise "Environments") or with `n8n export:workflow` / `n8n import:workflow` plus
  per-environment credentials of the same name.
- Use `$vars` (Variables) or a config sub-workflow for environment-specific values.
