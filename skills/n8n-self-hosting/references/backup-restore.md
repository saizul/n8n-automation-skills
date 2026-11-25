# Backup and restore

A complete n8n backup has three parts. Missing any one of them makes the others much
less useful.

| Part | Why | How |
| --- | --- | --- |
| Postgres database | Workflows, credentials (encrypted), executions, users, settings | `pg_dump` |
| `N8N_ENCRYPTION_KEY` | Decrypts credentials in the database and in exports | Password manager / secret store, kept apart from the dumps |
| `/home/node/.n8n` volume | Config file (holds the key if it was auto-generated), filesystem binary data, SSH keys for Git | Volume archive |

Workflow JSON exports are a convenient extra (diffable, importable into another instance).

## Commands (run in the compose directory; the user confirms each)

```bash
STAMP=$(date +%F-%H%M)
mkdir -p backups

# 1. Database (custom format, compressed)
docker compose exec -T postgres pg_dump -U n8n -d n8n -Fc > "backups/n8n-db-$STAMP.dump"

# 2. Workflows as JSON (one file per workflow)
docker compose exec -T -u node n8n sh -c \
  'rm -rf /tmp/wf && mkdir -p /tmp/wf && n8n export:workflow --all --separate --output=/tmp/wf >/dev/null && tar -C /tmp -cz wf' \
  > "backups/n8n-workflows-$STAMP.tar.gz"

# 3. Credentials, still encrypted with N8N_ENCRYPTION_KEY (do NOT use --decrypted for backups)
docker compose exec -T -u node n8n sh -c \
  'n8n export:credentials --all --output=/tmp/creds.json >/dev/null && cat /tmp/creds.json && rm /tmp/creds.json' \
  > "backups/n8n-credentials-$STAMP.json"
chmod 600 backups/*

# 4. Data volume (stop n8n first for a consistent copy if you use filesystem binary data)
docker run --rm -v "$(basename "$PWD")_n8n_data:/data:ro" -v "$PWD/backups:/out" alpine \
  tar -C /data -czf "/out/n8n-data-$STAMP.tar.gz" .
```

The volume name is `<project>_n8n_data`; check with `docker volume ls`.

Ship backups off the server (restic, rclone, S3 with versioning/object lock) and keep at
least 7 daily + 4 weekly copies. Encrypt them: the credentials file plus the key would
give access to every connected service.

## Schedule

A systemd timer or cron job running the commands above nightly, plus a check that the
newest dump is less than 26 hours old (alert if not). Do not schedule backups from inside
n8n only: if n8n is down, so are its backups.

## Restore drill (do it before you need it)

On a scratch server or a separate compose project:

```bash
# Same N8N_VERSION and N8N_ENCRYPTION_KEY as the source in .env
docker compose up -d postgres redis
docker compose exec -T postgres pg_restore -U n8n -d n8n --clean --if-exists < backups/n8n-db-STAMP.dump
docker compose up -d
```

Verify: log in, open three workflows, open a credential (it must decrypt), run a manual
test of one workflow, check the executions list. Keep new instances' workflows
**unpublished** until you are sure (otherwise schedules and polling triggers run twice:
once on the old, once on the new server).

Alternative restore from JSON exports into a fresh instance:

```bash
docker compose exec -u node n8n n8n import:credentials --input=/tmp/creds.json
docker compose exec -u node n8n n8n import:workflow --separate --input=/tmp/wf
```

Imported credentials decrypt only with the same encryption key.

## Moving to a new server

1. Lower DNS TTL a day ahead.
2. Unpublish/stop triggers on the old server or stop it, take the backup.
3. Restore on the new server with the same key and version, verify.
4. Switch DNS / proxy, then upgrade if planned (one change at a time).
5. Re-register webhooks if the public URL changed (providers keep the old URL).
