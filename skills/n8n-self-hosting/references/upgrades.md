# Upgrading n8n

## Routine upgrade

1. Read the release notes between your version and the target
   (https://docs.n8n.io/release-notes/). Look for "breaking changes".
2. Back up (database dump at minimum; see backup-restore.md).
3. Change `N8N_VERSION` in `.env` (all n8n services use the same image variable, so
   main, workers and webhook processors move together).
4. Pull and restart:
   ```bash
   docker compose pull n8n n8n-worker
   docker compose up -d
   docker compose logs -f n8n      # wait for migrations to finish and "Editor is now accessible"
   ```
5. Verify: `/healthz`, log in, run a test of a critical workflow, check a worker picks up
   jobs, check scheduled workflows on their next run.
6. Rollback: database migrations are not reversed automatically. Stop n8n, restore the
   dump taken in step 2, set the old version, start.

Prefer small steps (a few minor versions at a time) over jumping a year ahead. Never run
two different n8n versions against the same database.

## Upgrading to 2.x from 1.x

Key breaking changes in 2.0 (see the official "2.0 breaking changes" page for the full,
current list):

| Change | What to do |
| --- | --- |
| MySQL/MariaDB storage removed | Migrate to PostgreSQL (or SQLite) **before** upgrading |
| Task runners on by default; Code runs in runners | Test Code nodes; set module allow-lists on the runner |
| `N8N_BLOCK_ENV_ACCESS_IN_NODE` defaults to `true` | Replace `$env` in expressions/Code with credentials or `$vars`, or set it to `false` knowingly |
| `ExecuteCommand` and `LocalFileTrigger` nodes disabled by default (`NODES_EXCLUDE`) | Re-enable only if required |
| `N8N_RESTRICT_FILE_ACCESS_TO` defaults to `~/.n8n-files` | Move files there or set the variable |
| Settings file must be `0600` (`N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS`) | Fix permissions, or set `false` where permissions are unsupported |
| OAuth callbacks require authentication (`N8N_SKIP_AUTH_ON_OAUTH_CALLBACK=false`) | Reconnect OAuth credentials if callbacks fail |
| Start node removed | Replace with Manual Trigger / Execute Workflow Trigger |
| Pyodide Python Code removed | Python Code needs external task runners |
| `N8N_DEFAULT_BINARY_DATA_MODE=default` removed | Use `filesystem`, `database` or `s3` |
| `n8n --tunnel` removed | Use a real public URL or your own tunnel |
| `update:workflow` CLI replaced by `publish:workflow` / `unpublish:workflow` | Update scripts |
| Sub-workflow that waits returns the child's output to the parent | Re-test parent/child flows with Wait nodes |
| `.env` parsing changed (dotenv upgrade: backticks, multiline, `#`) | Quote values containing `#` |
| `QUEUE_WORKER_MAX_STALLED_COUNT` removed | Remove it; stalled jobs are no longer retried automatically |

Check the 2.0 migration tooling offered by recent 1.x releases (instance settings) before
upgrading, and run `scripts/n8n_env_check.py` on the new configuration: it flags removed
variables (`N8N_BASIC_AUTH_*`, binary mode `default`, MySQL) and risky overrides.

## Pinning

- Use `docker.n8n.io/n8nio/n8n:<exact version>`; avoid `latest`/`next` (n8n plans to
  replace them with `stable`/`beta`).
- Pin Postgres and Redis major versions too. A Postgres major upgrade needs
  `pg_dump`/`pg_restore` or `pg_upgrade`; never just change the image tag.
