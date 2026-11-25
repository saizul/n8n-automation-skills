"""Shared helpers: import skill scripts by path and locate fixtures."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
WORKFLOWS = FIXTURES / "workflows"


def load_script(skill: str, filename: str):
    path = SKILLS / skill / "scripts" / filename
    name = f"skill_{skill.replace('-', '_')}_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def run_script(skill: str, filename: str, *args: str, stdin: str | None = None, env: dict | None = None):
    path = SKILLS / skill / "scripts" / filename
    return subprocess.run(
        [sys.executable, str(path), *args],
        capture_output=True,
        text=True,
        input=stdin,
        timeout=60,
        check=False,
        env=env,
    )


def load_wf(name: str) -> dict:
    return json.loads((WORKFLOWS / name).read_text())


def make_wf(nodes: list[dict], connections: dict | None = None, **extra) -> dict:
    """Build a minimal workflow; nodes are dicts with at least name and type."""
    full = []
    for i, n in enumerate(nodes):
        full.append({"parameters": {}, "typeVersion": 1, "position": [i * 200, 0], "id": f"id-{i}", **n})
    return {"name": "test", "nodes": full, "connections": connections or {}, **extra}


def chain(*names: str) -> dict:
    """Connections for a linear chain of node names."""
    return {a: {"main": [[{"node": b, "type": "main", "index": 0}]]} for a, b in zip(names, names[1:])}


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES
