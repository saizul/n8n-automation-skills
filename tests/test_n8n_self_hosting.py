import json

import pytest

from conftest import FIXTURES, ROOT, load_script, run_script

SKILL = "n8n-self-hosting"
SCRIPT = "n8n_env_check.py"
mod = load_script(SKILL, SCRIPT)
F = FIXTURES / SKILL
ASSETS = ROOT / "skills" / SKILL / "assets"
KEY = "aaaabbbbccccddddeeeeffff00001111"


def checks(findings):
    return {(f.check, f.service) for f in findings}


def test_bad_env():
    env = mod.parse_env((F / "bad.env").read_text())
    got = {c for c, _ in checks(mod.check_env(env, "env"))}
    assert {
        "env.db_sqlite",
        "env.queue_no_redis",
        "env.basic_auth_legacy",
        "env.binary_mode_removed",
        "env.code_allow_all",
        "env.encryption_key_weak",
        "env.prune_off",
        "env.webhook_url",
        "env.env_access",
        "env.secure_cookie",
        "env.timezone",
    } <= got
    sqlite = [f for f in mod.check_env(env, "env") if f.check == "env.db_sqlite"][0]
    assert sqlite.severity == "high"  # SQLite with queue mode


def test_good_env_clean():
    env = mod.parse_env((F / "good.env").read_text())
    assert env["DB_TYPE"] == "postgresdb"  # export prefix
    assert env["DB_POSTGRESDB_PASSWORD"] == "fixture-db-pass"  # quotes stripped
    assert env["GENERIC_TIMEZONE"] == "Asia/Dhaka"  # inline comment stripped
    assert mod.check_env(env, "env") == []


def test_env_example_asset_only_needs_the_key():
    env = mod.parse_env((ASSETS / ".env.example").read_text())
    got = {(f.check, f.severity) for f in mod.check_env(env, "env")}
    assert ("env.encryption_key_missing", "high") in got
    assert {c for c, s in got if s in ("medium", "high")} == {"env.encryption_key_missing"}


def test_good_compose_clean():
    data = json.loads((F / "compose-good.json").read_text())
    assert mod.check_compose(data, {}) == []


def test_bad_compose():
    data = json.loads((F / "compose-bad.json").read_text())
    got = checks(mod.check_compose(data, {}))
    assert ("env.encryption_key_mismatch", "n8n, worker") in got
    assert ("env.worker_not_queue", "worker") in got
    assert ("compose.image_unpinned", "n8n") in got
    assert ("compose.no_data_volume", "n8n") in got
    assert ("compose.port_public", "n8n") in got
    assert ("compose.db_public", "postgres") in got
    assert ("compose.version_mismatch", "n8n, worker") in got
    assert ("env.basic_auth_legacy", "n8n") in got
    assert ("env.webhook_url", "n8n") in got


def test_queue_without_worker_and_no_n8n():
    data = json.loads((F / "compose-good.json").read_text())
    del data["services"]["n8n-worker"]
    assert ("compose.queue_no_worker", "-") in checks(mod.check_compose(data, {}))
    assert ("compose.no_n8n", "-") in checks(mod.check_compose({"services": {"db": {"image": "postgres:16"}}}, {}))


def test_mysql_and_missing_key_and_env_file_merge():
    env = {"DB_TYPE": "mysqldb"}
    got = {c for c, _ in checks(mod.check_env(env, "x"))}
    assert {"env.db_mysql", "env.encryption_key_missing"} <= got
    data = json.loads((F / "compose-good.json").read_text())
    for svc in ("n8n", "n8n-worker"):
        del data["services"][svc]["environment"]["N8N_ENCRYPTION_KEY"]
    assert ("env.encryption_key_missing", "n8n") in checks(mod.check_compose(data, {}))
    # a key from --env applies to every service
    assert ("env.encryption_key_missing", "n8n") not in checks(mod.check_compose(data, {"N8N_ENCRYPTION_KEY": KEY}))


@pytest.mark.parametrize(
    "svc,role",
    [
        ({"image": "docker.n8n.io/n8nio/n8n:2.4.1"}, "main"),
        ({"image": "n8nio/n8n", "command": "worker --concurrency=5"}, "worker"),
        ({"image": "n8nio/n8n:2.4.1", "command": ["webhook"]}, "webhook"),
        ({"image": "n8nio/runners:2.4.1"}, None),
        ({"image": "postgres:16"}, None),
    ],
)
def test_role_of(svc, role):
    assert mod.role_of(svc) == role


@pytest.mark.parametrize(
    "ports,public",
    [
        (["5678:5678"], ["5678"]),
        (["127.0.0.1:5678:5678"], []),
        (["0.0.0.0:5432:5432"], ["5432"]),
        ([{"target": 5678, "published": "5678", "host_ip": "127.0.0.1"}], []),
        ([{"target": 5678, "published": "8080"}], ["8080"]),
    ],
)
def test_published_public(ports, public):
    assert mod.published_public({"ports": ports}) == public


def test_compose_yaml_asset_parses():
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load((ASSETS / "docker-compose.yml").read_text())
    assert {"postgres", "redis", "n8n", "n8n-worker"} <= set(data["services"])
    worker = data["services"]["n8n-worker"]
    assert worker["command"][0] == "worker" and worker["environment"]["EXECUTIONS_MODE"] == "queue"
    assert data["services"]["n8n"]["ports"] == ["127.0.0.1:5678:5678"]
    assert "ports" not in data["services"]["postgres"] and "ports" not in data["services"]["redis"]


def test_cli_never_prints_values():
    r = run_script(SKILL, SCRIPT, "--compose", str(F / "compose-bad.json"), "--json")
    assert r.returncode == 1
    assert KEY not in r.stdout and "fixture-db-pass" not in r.stdout
    r = run_script(SKILL, SCRIPT, "--env", str(F / "bad.env"))
    assert r.returncode == 1 and "changeme" not in r.stdout
    assert run_script(SKILL, SCRIPT, "--env", str(F / "good.env")).returncode == 0
    assert run_script(SKILL, SCRIPT, "--compose", str(F / "compose-good.json"), "--fail-on", "info").returncode == 0
    r = run_script(SKILL, SCRIPT, "--env", str(F / "good.env"), "--role", "worker")
    assert r.returncode == 1 and "EXECUTIONS_MODE=queue" in r.stdout
    assert run_script(SKILL, SCRIPT, "--env", "missing.env").returncode == 2
    assert run_script(SKILL, SCRIPT).returncode == 2  # argparse error: no input
    assert run_script(SKILL, SCRIPT, "--help").returncode == 0
