---
name: n8n-self-hosting
description: Run self-hosted n8n in production with Docker Compose - PostgreSQL, Redis and queue mode (main + workers, optional webhook processors), reverse proxy and WEBHOOK_URL, encryption key handling, execution pruning, task runners, backups (pg_dump, workflow/credential export, the key) with a restore drill, and safe version upgrades including the n8n 2.0 breaking changes. Ships a ready docker-compose.yml and .env.example, plus a checker for .env / `docker compose config` output that flags missing or mismatched N8N_ENCRYPTION_KEY, SQLite or removed MySQL, queue mode without Redis or workers, unpinned images, missing data volume, public ports, removed variables and risky Code-node settings without printing any values. Use when installing, moving, scaling, backing up, upgrading or debugging a self-hosted n8n (Docker, VPS, queue mode, workers).
license: MIT
compatibility: Python 3.8+ standard library (PyYAML optional for compose YAML). Commands assume Docker Compose v2 on Linux.
metadata:
  author: Saizul Amin
  version: "0.1.0"
---

# n8n self-hosting

Read-only first: inspect and report, then change the server only with the user's explicit
OK for each step. Never print or paste the encryption key, database password or `.env`.

Paths below are relative to this skill's folder (in Claude Code: `${CLAUDE_SKILL_DIR}`).

## When to use

- "Install n8n on my VPS with Docker", "move from SQLite to Postgres", "enable queue mode"
- Executions slow or stuck, UI freezes during big runs, database growing fast
- Backups, restore, moving n8n to a new server
- Upgrading n8n (especially 1.x to 2.x), "credentials could not be decrypted" after a move
- Reviewing someone's `docker-compose.yml` / `.env` for n8n

## When not to use

- n8n Cloud (no server access; settings are in the admin panel)
- Generic Docker/nginx/server hardening beyond n8n: use your server/compose review tooling
- Workflow-level problems: `n8n-workflow-lint`, `n8n-error-handling`

## Workflow

1. **Inventory** (read-only). Ask for or run, in the compose directory:
   ```bash
   docker compose ps
   docker compose config --format json > /tmp/n8n-compose.json   # contains secrets: never paste it
   docker compose exec n8n n8n --version
   ```
2. **Check the configuration** (values are hashed or hidden, never printed):
   ```bash
   python3 scripts/n8n_env_check.py --compose /tmp/n8n-compose.json
   python3 scripts/n8n_env_check.py --env .env                     # env file only
   python3 scripts/n8n_env_check.py --env .env --role worker --json
   ```
   Delete `/tmp/n8n-compose.json` afterwards (`shred -u` or `rm`), with the user's OK.
3. **Decide the topology:**
   - < ~5k executions/day, no long CPU-heavy runs: single `n8n` + Postgres (regular mode).
   - More load, long runs, or the UI must stay responsive: queue mode with Redis and 1+
     workers ([assets/docker-compose.yml](assets/docker-compose.yml)).
   - Heavy webhook traffic: add webhook processors (`command: webhook`) behind the proxy.
   Details, sizing and task runners: [references/queue-mode.md](references/queue-mode.md).
4. **Install or fix** using the assets. Copy
   [assets/.env.example](assets/.env.example) to `.env` (chmod 600), generate the key with
   `openssl rand -hex 32`, pin `N8N_VERSION`, set the public URL and timezone, then
   `docker compose config -q && docker compose up -d`. For an existing instance, the key
   must stay the same (read it from the user's secret store or from
   `/home/node/.n8n/config` inside the volume; do not display it).
5. **Back up** before any change and on a schedule:
   [references/backup-restore.md](references/backup-restore.md) (Postgres dump, workflow
   and credential export, the encryption key, restore drill).
6. **Upgrade** with [references/upgrades.md](references/upgrades.md): read the release
   notes, back up, bump the pinned version on all n8n services at once, watch migrations
   in the logs, verify, keep the rollback path (restore the dump; migrations do not
   roll back).
7. **Verify**: `docker compose ps` healthy, `curl -fsS https://n8n.example.com/healthz`,
   a test webhook works through the proxy, one scheduled workflow fires at the right local
   time, and a worker picks up jobs (`docker compose logs -f n8n-worker`).

## Output

```
n8n config check (compose): 2 high, 3 medium
HIGH  worker: EXECUTIONS_MODE=queue missing -> worker idles, executions queue up
HIGH  n8n, worker: N8N_ENCRYPTION_KEY differs (hash mismatch) -> workers cannot decrypt credentials
MED   n8n: image n8nio/n8n (latest) -> pin docker.n8n.io/n8nio/n8n:<version>
MED   n8n: WEBHOOK_URL not set -> providers get http://localhost:5678/webhook/...
MED   n8n: no /home/node/.n8n volume

Plan (each step needs your OK):
1. Backup: pg_dump + export workflows/credentials (commands below)
2. Edit .env: same key on all services, WEBHOOK_URL, pin N8N_VERSION=...
3. docker compose up -d; verify healthz, worker logs, a test webhook
```
