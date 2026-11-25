# Queue mode, scaling and runtime settings

## How queue mode works

- **main** (`n8n` / `n8n start`): editor, REST API, triggers, schedules, webhooks (unless
  webhook processors take them). It puts executions on a Redis (Bull) queue.
- **workers** (`n8n worker`): take jobs from Redis and run them. Concurrency defaults to
  10 per worker (`--concurrency=N`; n8n recommends 5 or more).
- **webhook processors** (`n8n webhook`, optional): receive production webhooks and
  enqueue them; route `/webhook/*` to them at the load balancer. Setting
  `N8N_DISABLE_PRODUCTION_MAIN_PROCESS=true` stops main from handling production webhooks.
- All processes share Postgres, Redis, `EXECUTIONS_MODE=queue`, the **same**
  `N8N_ENCRYPTION_KEY` and the **same n8n version**.

Required variables on every n8n process:

| Variable | Value |
| --- | --- |
| `EXECUTIONS_MODE` | `queue` |
| `N8N_ENCRYPTION_KEY` | identical everywhere |
| `DB_TYPE`, `DB_POSTGRESDB_*` | Postgres (SQLite is not recommended with queue mode) |
| `QUEUE_BULL_REDIS_HOST`, `QUEUE_BULL_REDIS_PORT` | e.g. `redis`, `6379` (optional `_USERNAME`, `_PASSWORD`, `_DB`) |
| `QUEUE_HEALTH_CHECK_ACTIVE` | `true` on workers to expose `/healthz` and `/healthz/readiness` |

Useful:

| Variable | Purpose |
| --- | --- |
| `OFFLOAD_MANUAL_EXECUTIONS_TO_WORKERS=true` | Manual editor runs also go to workers |
| `N8N_GRACEFUL_SHUTDOWN_TIMEOUT` | Seconds a worker gets to finish jobs on stop (default 30) |
| `QUEUE_BULL_REDIS_TIMEOUT_THRESHOLD` | How long to wait for Redis before exiting (ms) |
| `N8N_REDIS_KEY_PREFIX` | Separate prefix when several deployments share one Redis |

Multi-main (several main processes with leader election) is an Enterprise feature
(`N8N_MULTI_MAIN_SETUP_ENABLED=true`, sticky sessions).

## Sizing rules of thumb

- Start with 1 worker, concurrency 10, 2 vCPU / 4 GB for main + 1 worker on one VPS.
- CPU-bound workflows (Code, big JSON): more workers with lower concurrency.
- I/O-bound workflows (HTTP APIs): higher concurrency (10-20) is fine.
- Memory: each concurrent execution holds its items in memory; big files belong in
  binary data storage, not JSON.
- Scale workers: `docker compose up -d --scale n8n-worker=3`.

## Binary data

`N8N_DEFAULT_BINARY_DATA_MODE`: `filesystem`, `database` (default in queue mode) or `s3`
(the old `default` in-memory mode was removed in 2.0). `filesystem` with workers requires
the same shared volume path on main, workers and webhook processors.

## Task runners (Code node)

Since n8n 2.0 task runners are enabled by default and every Code node runs in a runner.
Internal mode (runner launched by n8n) works out of the box. External mode uses the
separate `n8nio/runners` image (the main image no longer bundles the external runner).
Module allow-lists (`NODE_FUNCTION_ALLOW_BUILTIN`, `NODE_FUNCTION_ALLOW_EXTERNAL`) must be
set where the runner runs; check the runner's config if a module stays "disallowed".

## Reverse proxy

- Terminate TLS at nginx/Caddy/Traefik; publish n8n only on `127.0.0.1:5678`.
- Set `WEBHOOK_URL=https://n8n.example.com/` (from 2.30 also `N8N_WEBHOOK_URL`),
  `N8N_HOST`, `N8N_PROTOCOL=https`, `N8N_PROXY_HOPS=1`.
- The editor uses a push connection (WebSocket by default): forward `Upgrade`/`Connection`
  headers.
- Allow bodies up to the payload limit (`N8N_PAYLOAD_SIZE_MAX`, default 16 MB).
- Rate-limit `/webhook/` and restrict the editor: see `n8n-webhook-security`.

nginx minimum:

```nginx
location / {
    proxy_pass http://127.0.0.1:5678;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
    proxy_read_timeout 300s;
    client_max_body_size 16m;
}
```

## Database hygiene

- Pruning: `EXECUTIONS_DATA_PRUNE=true`, `EXECUTIONS_DATA_MAX_AGE` (hours),
  `EXECUTIONS_DATA_PRUNE_MAX_COUNT` (cap on stored executions).
- Save less: Workflow settings > "Save successful production executions: Do not save" for
  high-volume workflows (keep failed ones).
- Check size: `docker compose exec postgres psql -U n8n -c "SELECT pg_size_pretty(pg_database_size('n8n'));"`
- After a big prune, Postgres reuses space; `VACUUM FULL` (locks tables) only in a maintenance window.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Executions stay "queued" / "new" | Worker running? Same `EXECUTIONS_MODE=queue`, Redis host reachable from the worker, same version |
| "Credentials could not be decrypted" | Different `N8N_ENCRYPTION_KEY` (new volume, new server, worker without key) |
| Webhook URLs show `localhost:5678` | `WEBHOOK_URL` not set |
| Schedules fire at the wrong hour | `GENERIC_TIMEZONE` / workflow timezone |
| Editor "Connection lost" | Proxy not forwarding WebSocket upgrade headers |
| `413` on uploads | Proxy `client_max_body_size` / `N8N_PAYLOAD_SIZE_MAX` |
| Code node "module is disallowed" | `NODE_FUNCTION_ALLOW_BUILTIN` on the runner |
| Container OOM-killed | Large items in memory: batch, binary storage, more RAM, lower concurrency |
