# AGENTS.md

Guidance for Codex CLI and other coding agents that use or work on this repository.

## Using the skills

The skills live in `skills/<name>/SKILL.md` and follow the Agent Skills format. Codex
discovers them when they are copied to `~/.agents/skills/` or `.agents/skills/` (see
README "Install"). If you are reading this file inside the repository instead:

1. Pick the skill whose `description` matches the user's request:

   | Request is about | Skill |
   | --- | --- |
   | planning or restructuring a workflow, duplicates, batching, rate limits | `n8n-workflow-design` |
   | reviewing / cleaning up a workflow JSON, CI checks for exports | `n8n-workflow-lint` |
   | alerts on failures, retries, dead letters, replay | `n8n-error-handling` |
   | Webhook / Form / Chat trigger security, HMAC signatures, rate limits | `n8n-webhook-security` |
   | expressions, `$json`, dates, Code node data access, [undefined] | `n8n-expressions-cheatsheet` |
   | Docker, Postgres, queue mode, workers, backups, upgrades | `n8n-self-hosting` |
   | AI Agent nodes, tools, memory, LLM cost | `n8n-ai-agent-workflows` |
   | documentation, diagrams, handover | `n8n-workflow-docs` |

2. Read that `SKILL.md` completely and follow its Workflow section in order.
3. Paths such as `scripts/x.py`, `references/y.md` and `assets/z.json` are relative to the
   skill folder. `${CLAUDE_SKILL_DIR}` in the text means that folder; Codex does not expand it.
4. Read files in `references/` only when a workflow step points to them.

## Safety rules (apply to every skill)

- The scripts are read-only and work on exported files. Unless the user explicitly asks,
  do not use the n8n REST interface of their instance, do not publish workflows and do
  not change server configuration; show the exact command or change first and wait for confirmation.
- Never print, paste or commit secrets: API keys, tokens, webhook signing secrets,
  `N8N_ENCRYPTION_KEY`, database passwords, `.env` contents, decrypted credential exports.
  The scripts hide values; keep it that way when summarising.
- Workflow exports and pinned data can contain personal data. Work on the files locally;
  quote only the parts needed.
- When a hard-coded secret is found, tell the user to rotate it; moving it is not enough.
- For changes to production workflows, prefer a copy or a staging instance and tell the
  user how to roll back.

## Working on this repository

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python tools/validate_skills.py   # frontmatter, sections, links, assets, marketplace listing
ruff check . && ruff format --check .
pytest
```

- Scripts: Python 3.8+ standard library only, `#!/usr/bin/env python3`, `argparse` with
  `--help`, `--json` where output is structured, exit 0/1/2 (ok / findings / error).
- Scripts must stay standalone (one file each, no shared module) so a single skill folder
  can be copied on its own.
- Every script needs tests in `tests/test_<skill_with_underscores>.py`. Shared workflow
  fixtures are in `tests/fixtures/workflows/`, skill-specific ones in
  `tests/fixtures/<skill>/`. Use `example.com`, documentation IPs and obviously fake
  secrets built so that secret scanners do not match them.
- Keep each `SKILL.md` under 400 lines; move detail to `references/`.
- A new skill must be added to `.claude-plugin/marketplace.json` (`plugins[0].skills`), the
  README table, the table above and `CHANGELOG.md`. The validator enforces the manifest.
