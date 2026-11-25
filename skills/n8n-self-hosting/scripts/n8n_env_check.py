#!/usr/bin/env python3
"""Check a self-hosted n8n configuration (.env and/or Docker Compose) for production problems.

Inputs (at least one):
  --env FILE       a .env file with n8n variables (repeatable; later files win)
  --compose FILE   the resolved compose config. Best: ``docker compose config --format json``
                   (merges env_file and interpolation). YAML works when PyYAML is installed.

Checks the encryption key (set, strong, identical on main/worker/webhook - compared by hash,
never printed), database (Postgres vs SQLite vs removed MySQL), queue mode wiring (Redis host,
worker services, EXECUTIONS_MODE on every process, same image tag), public URL settings
(WEBHOOK_URL / N8N_WEBHOOK_URL, N8N_PROXY_HOPS), timezone, pruning, removed or risky
variables (N8N_BASIC_AUTH_*, binary mode 'default', NODE_FUNCTION_ALLOW_*=*, env access),
and compose issues (unpinned image, missing /home/node/.n8n volume, ports on 0.0.0.0,
public Postgres/Redis). Values are never printed, only variable names.

Exit codes: 0 = no findings at or above --fail-on, 1 = findings, 2 = error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass

SEVERITIES = ["info", "low", "medium", "high"]
PLACEHOLDER_RE = re.compile(r"change|replace|your|example|secret|xxx|todo|n8n|password|^(.)\1+$", re.I)


@dataclass
class Finding:
    check: str
    severity: str
    service: str
    title: str
    fix: str = ""


def parse_env(text: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        env[key] = value
    return env


def load_compose(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        import yaml  # noqa: PLC0415
    except ImportError as exc:
        raise ValueError("YAML needs PyYAML; or run: docker compose config --format json > compose.json") from exc
    return yaml.safe_load(text) or {}


def service_env(svc: dict) -> dict[str, str]:
    env = svc.get("environment") or {}
    if isinstance(env, list):
        out = {}
        for item in env:
            k, _, v = str(item).partition("=")
            out[k] = v
        return out
    return {str(k): "" if v is None else str(v) for k, v in env.items()}


def role_of(svc: dict) -> str | None:
    image = str(svc.get("image", ""))
    if not re.search(r"(^|/)n8nio/n8n(:|@|$)|(^|/)n8n(:|@|$)", image):
        return None
    cmd = svc.get("command") or ""
    cmd = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
    if re.search(r"\bworker\b", cmd):
        return "worker"
    if re.search(r"\bwebhook\b", cmd):
        return "webhook"
    return "main"


def image_tag(image: str) -> str:
    if "@sha256:" in image:
        return image.split("@", 1)[1]
    last = image.rsplit("/", 1)[-1]
    return last.split(":", 1)[1] if ":" in last else ""


def fp(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def check_env(env: dict[str, str], service: str, role: str = "main") -> list[Finding]:
    f: list[Finding] = []

    def add(check, sev, title, fix=""):
        f.append(Finding(check, sev, service, title, fix))

    queue = env.get("EXECUTIONS_MODE", "regular") == "queue"
    key = env.get("N8N_ENCRYPTION_KEY", "")
    if not key and not env.get("N8N_ENCRYPTION_KEY_FILE"):
        add(
            "env.encryption_key_missing",
            "high",
            "N8N_ENCRYPTION_KEY is not set: n8n generates one inside /home/node/.n8n; lose that volume and "
            "every stored credential is unreadable (workers also need the same key)",
            "generate once (openssl rand -hex 32), store it in your secret manager, set it on every n8n process",
        )
    elif key and (len(key) < 24 or PLACEHOLDER_RE.search(key)):
        add(
            "env.encryption_key_weak",
            "medium",
            "N8N_ENCRYPTION_KEY looks short or like a placeholder",
            "use 32+ random bytes; changing it later means re-entering credentials (export them first)",
        )
    db = env.get("DB_TYPE", "sqlite")
    if db in ("mysqldb", "mariadb"):
        add(
            "env.db_mysql",
            "high",
            f"DB_TYPE={db}: MySQL/MariaDB storage is not supported since n8n 2.0",
            "migrate to PostgreSQL before upgrading",
        )
    elif db == "sqlite":
        add(
            "env.db_sqlite",
            "high" if queue else "info",
            "SQLite database" + (" with queue mode (not supported for multiple processes)" if queue else ""),
            "use PostgreSQL (DB_TYPE=postgresdb) for production",
        )
    elif db == "postgresdb":
        if not env.get("DB_POSTGRESDB_PASSWORD") and not env.get("DB_POSTGRESDB_PASSWORD_FILE"):
            add(
                "env.db_password_missing",
                "medium",
                "DB_POSTGRESDB_PASSWORD is not set",
                "set it (or DB_POSTGRESDB_PASSWORD_FILE with a Docker secret)",
            )
        if not env.get("DB_POSTGRESDB_HOST"):
            add(
                "env.db_host_missing",
                "medium",
                "DB_POSTGRESDB_HOST is not set (defaults to localhost)",
                "set the Postgres service name",
            )
    if queue:
        if not env.get("QUEUE_BULL_REDIS_HOST"):
            add(
                "env.queue_no_redis",
                "high",
                "queue mode without QUEUE_BULL_REDIS_HOST (defaults to localhost, wrong inside a container)",
                "set QUEUE_BULL_REDIS_HOST=redis",
            )
        if role == "worker" and env.get("QUEUE_HEALTH_CHECK_ACTIVE", "").lower() != "true":
            add(
                "env.worker_health",
                "info",
                "worker health endpoint is off",
                "QUEUE_HEALTH_CHECK_ACTIVE=true enables /healthz and /healthz/readiness for Docker healthchecks",
            )
        if role == "main" and env.get("OFFLOAD_MANUAL_EXECUTIONS_TO_WORKERS", "").lower() != "true":
            add(
                "env.manual_offload",
                "info",
                "manual (editor) executions still run on the main process",
                "OFFLOAD_MANUAL_EXECUTIONS_TO_WORKERS=true keeps heavy test runs off the UI process",
            )
    elif role in ("worker", "webhook"):
        add(
            "env.worker_not_queue",
            "high",
            f"{role} process without EXECUTIONS_MODE=queue",
            "set EXECUTIONS_MODE=queue on main, workers and webhook processors",
        )
    if role == "main":
        url = env.get("N8N_WEBHOOK_URL") or env.get("WEBHOOK_URL")
        if not url:
            add(
                "env.webhook_url",
                "medium",
                "WEBHOOK_URL / N8N_WEBHOOK_URL not set: webhook URLs are built from host/port (often http://localhost:5678/)",
                "set WEBHOOK_URL=https://n8n.example.com/ (N8N_WEBHOOK_URL from 2.30)",
            )
        elif url.startswith("https://") and not env.get("N8N_PROXY_HOPS"):
            add(
                "env.proxy_hops",
                "low",
                "behind HTTPS but N8N_PROXY_HOPS is not set",
                "N8N_PROXY_HOPS=1 when one reverse proxy sits in front, so client IPs and protocol are right",
            )
        if not env.get("GENERIC_TIMEZONE"):
            add(
                "env.timezone",
                "low",
                "GENERIC_TIMEZONE not set: Schedule Triggers use America/New_York",
                "set GENERIC_TIMEZONE (and TZ) to your zone, e.g. Asia/Dhaka",
            )
        if env.get("EXECUTIONS_DATA_PRUNE", "").lower() == "false":
            add(
                "env.prune_off",
                "medium",
                "execution pruning is off: the database grows without limit",
                "EXECUTIONS_DATA_PRUNE=true, EXECUTIONS_DATA_MAX_AGE=336 (hours)",
            )
        if env.get("N8N_SECURE_COOKIE", "").lower() == "false":
            add(
                "env.secure_cookie",
                "low",
                "N8N_SECURE_COOKIE=false: the session cookie is also sent over plain HTTP",
                "serve n8n over HTTPS and remove the override",
            )
        if env.get("N8N_DIAGNOSTICS_ENABLED", "").lower() != "false":
            add(
                "env.diagnostics",
                "info",
                "telemetry is on (default)",
                "N8N_DIAGNOSTICS_ENABLED=false to opt out",
            )
    legacy = sorted(k for k in env if k.startswith("N8N_BASIC_AUTH_"))
    if legacy:
        add(
            "env.basic_auth_legacy",
            "medium",
            f"{', '.join(legacy)}: removed in n8n 1.0, does nothing",
            "remove them; use n8n user management (and SSO/2FA)",
        )
    if env.get("N8N_DEFAULT_BINARY_DATA_MODE") == "default":
        add(
            "env.binary_mode_removed",
            "medium",
            "N8N_DEFAULT_BINARY_DATA_MODE=default was removed in n8n 2.0",
            "use filesystem (single instance), database or s3",
        )
    elif queue and env.get("N8N_DEFAULT_BINARY_DATA_MODE") == "filesystem":
        add(
            "env.binary_filesystem_queue",
            "low",
            "filesystem binary data in queue mode needs the same shared volume on main, workers and webhooks",
            "share the volume, or use database / s3 mode",
        )
    for var in ("NODE_FUNCTION_ALLOW_BUILTIN", "NODE_FUNCTION_ALLOW_EXTERNAL"):
        if env.get(var, "").strip() == "*":
            add(
                "env.code_allow_all",
                "medium",
                f"{var}=* lets every Code node load any module (child_process, fs, ...)",
                f"list only what you need, e.g. {var}=crypto",
            )
    if env.get("N8N_BLOCK_ENV_ACCESS_IN_NODE", "").lower() == "false":
        add(
            "env.env_access",
            "low",
            "N8N_BLOCK_ENV_ACCESS_IN_NODE=false: expressions and Code nodes can read every env var (incl. DB password)",
            "keep the default (true) and use credentials or $vars",
        )
    if "NODES_EXCLUDE" in env and "executecommand" not in env["NODES_EXCLUDE"].lower():
        add(
            "env.execute_command",
            "medium",
            "NODES_EXCLUDE re-enables Execute Command (shell access for every workflow editor)",
            "keep n8n-nodes-base.executeCommand in NODES_EXCLUDE unless you really need it",
        )
    if env.get("N8N_RUNNERS_ENABLED", "").lower() == "false":
        add(
            "env.runners_off",
            "low",
            "task runners disabled: Code runs inside the n8n process",
            "remove N8N_RUNNERS_ENABLED=false (runners are the default since 2.0)",
        )
    return f


def published_public(svc: dict) -> list[str]:
    out = []
    for p in svc.get("ports") or []:
        if isinstance(p, dict):
            host_ip = str(p.get("host_ip", ""))
            if p.get("published") and host_ip in ("", "0.0.0.0", "::"):
                out.append(str(p.get("published")))
        else:
            parts = str(p).split(":")
            if len(parts) == 2 or (len(parts) == 3 and parts[0] in ("", "0.0.0.0")):
                out.append(parts[-2])
    return out


def check_compose(compose: dict, base_env: dict[str, str]) -> list[Finding]:
    f: list[Finding] = []
    services = compose.get("services") or {}
    n8n = {}
    for name, svc in services.items():
        role = role_of(svc or {})
        if role:
            n8n[name] = (role, svc)
    if not n8n:
        return [
            Finding(
                "compose.no_n8n",
                "high",
                "-",
                "no n8n service found (image n8nio/n8n)",
                "check --compose input",
            )
        ]
    keys: dict[str, str] = {}
    tags: dict[str, str] = {}
    queue_any = False
    for name, (role, svc) in n8n.items():
        env = {**base_env, **service_env(svc)}
        queue_any = queue_any or env.get("EXECUTIONS_MODE") == "queue"
        f += check_env(env, name, role)
        if env.get("N8N_ENCRYPTION_KEY"):
            keys[name] = fp(env["N8N_ENCRYPTION_KEY"])
        image = str(svc.get("image", ""))
        tag = image_tag(image)
        tags[name] = tag
        if tag in ("", "latest", "next", "stable", "beta"):
            f.append(
                Finding(
                    "compose.image_unpinned",
                    "medium",
                    name,
                    f"image '{image}' is not pinned to a version",
                    "pin e.g. docker.n8n.io/n8nio/n8n:2.x.y and upgrade deliberately",
                )
            )
        if role == "main":
            vols = svc.get("volumes") or []
            targets = [
                v.get("target") if isinstance(v, dict) else str(v).split(":")[1] if ":" in str(v) else "" for v in vols
            ]
            if "/home/node/.n8n" not in targets:
                f.append(
                    Finding(
                        "compose.no_data_volume",
                        "high" if not env.get("N8N_ENCRYPTION_KEY") else "medium",
                        name,
                        "no volume for /home/node/.n8n (config, encryption key, binary files are lost on recreate)",
                        "mount a named volume: n8n_data:/home/node/.n8n",
                    )
                )
            for port in published_public(svc):
                f.append(
                    Finding(
                        "compose.port_public",
                        "low",
                        name,
                        f"port {port} published on all interfaces (Docker bypasses UFW)",
                        f'bind to localhost behind the reverse proxy: "127.0.0.1:{port}:5678"',
                    )
                )
        if not svc.get("restart") and not (svc.get("deploy") or {}).get("restart_policy"):
            f.append(Finding("compose.no_restart", "low", name, "no restart policy", "restart: unless-stopped"))
    if len(set(keys.values())) > 1:
        f.append(
            Finding(
                "env.encryption_key_mismatch",
                "high",
                ", ".join(sorted(keys)),
                "N8N_ENCRYPTION_KEY differs between n8n services: workers cannot decrypt credentials",
                "use one key for main, workers and webhook processors",
            )
        )
    if len({t or "latest" for t in tags.values()}) > 1:
        f.append(
            Finding(
                "compose.version_mismatch",
                "high",
                ", ".join(sorted(tags)),
                "n8n services run different image versions",
                "main, workers and webhook processors must run the same version",
            )
        )
    roles = {r for r, _ in n8n.values()}
    if queue_any and "worker" not in roles:
        f.append(
            Finding(
                "compose.queue_no_worker",
                "high",
                "-",
                "queue mode but no worker service (command: worker): executions wait forever",
                "add a service running `worker` with the same image, env and key",
            )
        )
    for name, svc in services.items():
        image = str((svc or {}).get("image", ""))
        if re.search(r"(^|/)(postgres|redis|valkey)(:|$)", image):
            for port in published_public(svc):
                f.append(
                    Finding(
                        "compose.db_public",
                        "high",
                        name,
                        f"{image.split(':')[0]} port {port} published on all interfaces",
                        "remove the ports: entry; n8n reaches it over the compose network",
                    )
                )
    return f


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Check a self-hosted n8n .env / Docker Compose setup (values are never printed).",
        epilog="Exit codes: 0 = clean, 1 = findings at or above --fail-on, 2 = error.",
    )
    p.add_argument("--env", action="append", default=[], help=".env file (repeatable)")
    p.add_argument("--compose", help="docker compose config --format json output (or YAML with PyYAML)")
    p.add_argument("--role", default="main", choices=["main", "worker", "webhook"], help="role for --env-only checks")
    p.add_argument("--json", action="store_true", help="print JSON")
    p.add_argument("--fail-on", default="medium", choices=[*SEVERITIES, "none"])
    args = p.parse_args(argv)
    if not args.env and not args.compose:
        p.error("give --env and/or --compose")
    try:
        env: dict[str, str] = {}
        for path in args.env:
            with open(path, encoding="utf-8") as fh:
                env.update(parse_env(fh.read()))
        findings = check_compose(load_compose(args.compose), env) if args.compose else check_env(env, "env", args.role)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    uniq: dict[tuple, Finding] = {}
    for f in findings:
        uniq.setdefault((f.check, f.service), f)
    order = {s: i for i, s in enumerate(reversed(SEVERITIES))}
    findings = sorted(uniq.values(), key=lambda f: (order[f.severity], f.service, f.check))
    if args.json:
        print(json.dumps({"findings": [asdict(f) for f in findings]}, indent=2))
    else:
        counts = {s: sum(1 for f in findings if f.severity == s) for s in SEVERITIES}
        print("n8n config check: " + ", ".join(f"{counts[s]} {s}" for s in reversed(SEVERITIES)))
        for f in findings:
            print(f"{f.severity.upper():6} {f.service}: {f.title}")
            if f.fix:
                print(f"       fix: {f.fix}")
    if args.fail_on == "none":
        return 0
    worst = max((SEVERITIES.index(f.severity) for f in findings), default=-1)
    return 1 if worst >= SEVERITIES.index(args.fail_on) else 0


if __name__ == "__main__":
    sys.exit(main())
