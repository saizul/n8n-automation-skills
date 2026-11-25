# Error handling patterns

## Error Trigger data

The Error Trigger receives one item. Fields commonly used:

| Expression | Value |
| --- | --- |
| `$json.workflow.id`, `$json.workflow.name` | Failed workflow |
| `$json.execution.id` | Execution id |
| `$json.execution.url` | Link to the execution (needs a correct `WEBHOOK_URL`/editor URL) |
| `$json.execution.error.message`, `.stack` | Error |
| `$json.execution.lastNodeExecuted` | Node that failed |
| `$json.execution.mode` | `trigger`, `webhook`, ... |
| `$json.execution.retryOf` | Set when the execution was a retry |
| `$json.trigger.error` | Present instead of `execution` when the **trigger** itself failed (e.g. polling auth error) |

Use optional chaining (`$json.execution?.error?.message`) so the alert also works for
trigger failures. Notes:

- Error workflows run only for production executions, not manual runs in the editor.
- A workflow can use itself as error workflow, but a broken alert node then loops. Use a
  dedicated alert workflow and keep it simple (no external lookups that can also fail).
- Include the execution URL, not the input data: alerts go to chat tools; payloads may
  contain personal data.

## Choosing On Error

| Situation | Node setting | Result |
| --- | --- | --- |
| Any failure must stop and alert | On Error: Stop Workflow (default) | Execution fails, error workflow runs |
| Flaky API, timeouts, 429/5xx | Retry On Fail: 3 tries, wait 2000-5000 ms | Retries in place; fails after the last try |
| Some items fail, others must continue | On Error: Continue (using error output) | Failed items go to the error output with `$json.error` |
| Legacy "Continue On Fail" / Continue (regular output) | Avoid | Failed items flow on as normal items: downstream nodes process error objects |
| Rule broken in the data | Stop And Error node | Fails with your message; error workflow runs |
| Ignore a known harmless error | IF on `$json.error` after error output | Explicit, documented |

Retry tips:

- Retrying a POST that creates something can create duplicates. Add an
  `Idempotency-Key` header with a stable id from the data, or make it an upsert.
- For long outages, retries are not enough: dead-letter the item and replay later.
- For 429, combine batching + Wait with retries; respect `Retry-After` in a Code node if
  the API sends it.

## Dead letter and replay

Table:

```sql
CREATE TABLE IF NOT EXISTS dead_letter (
  id            bigserial PRIMARY KEY,
  workflow_id   text        NOT NULL,
  workflow_name text        NOT NULL,
  execution_id  text,
  node          text        NOT NULL,
  error         text        NOT NULL,
  payload       jsonb       NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  replayed_at   timestamptz
);
CREATE INDEX IF NOT EXISTS dead_letter_open ON dead_letter (workflow_id) WHERE replayed_at IS NULL;
```

Error-output branch (after the failing node):

1. Set node "Shape dead letter": `workflow_id = {{ $workflow.id }}`,
   `workflow_name = {{ $workflow.name }}`, `execution_id = {{ $execution.id }}`,
   `node = 'Upsert contact'`, `error = {{ $json.error.message ?? JSON.stringify($json.error) }}`,
   `payload = {{ JSON.stringify($('Loop over batches').item.json) }}` (the original input,
   not the error object).
2. Postgres insert into `dead_letter` (or an n8n Data table if you do not run Postgres).

Replay workflow ("Replay dead letters", manual or scheduled):

1. Postgres: `SELECT id, payload FROM dead_letter WHERE workflow_id = $1 AND replayed_at IS NULL ORDER BY id LIMIT 100`
2. Execute Workflow: the sub-workflow that processes one item, input from `payload`
3. On success: `UPDATE dead_letter SET replayed_at = now() WHERE id = $1`

This needs the processing step to be idempotent (see `n8n-workflow-design`).

## Alert content template

```
:rotating_light: <workflow name> failed
Node: <lastNodeExecuted>   Mode: <mode>   Retry of: <retryOf or ->
Error: <first 500 chars of message>
<execution url>
```

## Noise control

- One error workflow per team/channel; route by tag or name prefix with a Switch node.
- Deduplicate: before sending, check a small table or Redis key
  `alert:{workflow_id}:{hash(message)}` with a 30-minute TTL.
- Daily digest of `dead_letter` counts per workflow instead of one alert per item.
- Escalate: if the same workflow failed N times in an hour, page (PagerDuty/Opsgenie).

## Monitoring beyond the error workflow

- Executions that never start (worker down, queue stuck) do not trigger the error
  workflow. Add an external heartbeat: a Schedule Trigger workflow that pings an uptime
  service (healthchecks.io style) every few minutes; alert when pings stop.
- `/healthz` on main and (with `QUEUE_HEALTH_CHECK_ACTIVE=true`) on workers for container
  health checks; see `n8n-self-hosting`.
