# n8n-automation-skills

[![CI](https://github.com/saizul/n8n-automation-skills/actions/workflows/ci.yml/badge.svg)](https://github.com/saizul/n8n-automation-skills/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/saizul/n8n-automation-skills?sort=semver)](https://github.com/saizul/n8n-automation-skills/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Stars](https://img.shields.io/github/stars/saizul/n8n-automation-skills?style=social)](https://github.com/saizul/n8n-automation-skills/stargazers)

**Agent Skills that help Claude Code and Codex design, debug and secure n8n workflows - with tested scripts that read your workflow JSON instead of guessing.**

Eight skills for the work that comes up in every n8n project: structuring workflows,
catching mistakes before they reach production, alerting on failures, locking down
webhooks, fixing expressions, running n8n on your own server, building AI agents that do
not burn money, and documenting it all. Seven of them ship a standard-library-only Python
script that works offline on exported workflow JSON.

- **Deterministic checks.** Linters and auditors parse the export and report findings with
  node names and parameter paths, as text or JSON, with CI-friendly exit codes.
- **Safe by default.** Scripts are read-only, never call your n8n instance, and never print
  secret values (tokens, keys, passwords, the encryption key), only where they are.
- **Tested.** 160+ tests with realistic fixture workflows (webhooks, Stripe/Shopify
  signatures, AI agents, queue-mode compose files), run on Python 3.11-3.13 in CI.
- **Current n8n.** Written for n8n 1.x/2.x: task runners, `$env` blocking, removed MySQL
  support, `N8N_WEBHOOK_URL`, LangChain-based AI nodes.

## Skills

| Skill | What it does | Try asking |
| --- | --- | --- |
| [n8n-workflow-design](skills/n8n-workflow-design/SKILL.md) | Trigger choice, sub-workflow boundaries, idempotency (upsert, idempotency keys, processed-events table), batching, pagination, rate limits, waits; outputs a node-by-node plan | "Design an n8n flow that syncs paid Shopify orders to HubSpot without duplicates" |
| [n8n-workflow-lint](skills/n8n-workflow-lint/SKILL.md) | Lints workflow JSON: default node names, missing error workflow, hard-coded credentials and URLs, disabled/unused/unreachable nodes, dangling connections, webhook path clashes across workflows, long chains, pinned data | "Lint this workflow export before I activate it" |
| [n8n-error-handling](skills/n8n-error-handling/SKILL.md) | Importable Error Trigger alert workflow, retry and On Error strategy, dead-letter table + replay; audits a whole export for coverage, swallowed errors and unconnected error outputs | "Alert me in Slack whenever any production workflow fails" |
| [n8n-webhook-security](skills/n8n-webhook-security/SKILL.md) | Auth options, HMAC verification snippets (GitHub, Stripe, Shopify, Slack, generic) over the raw body, proxy rate limits; webhook audit script and a signer for test requests | "Secure my Stripe webhook in n8n" |
| [n8n-expressions-cheatsheet](skills/n8n-expressions-cheatsheet/SKILL.md) | `{{ }}` / `$json` / `$('Node').item` / Luxon / `$jmespath` patterns; script extracts every expression and flags literal braces, Python syntax, missing nodes, bad dot keys, `$input.item` in all-items Code | "Why does this expression return [undefined]?" |
| [n8n-self-hosting](skills/n8n-self-hosting/SKILL.md) | Docker Compose with Postgres + Redis queue mode and workers, backups and restore drill, upgrades incl. 2.0 breaking changes; checker for `.env` / `docker compose config` | "Move my n8n from SQLite to Postgres with queue mode" |
| [n8n-ai-agent-workflows](skills/n8n-ai-agent-workflows/SKILL.md) | Agent vs chain, system messages, tool design, per-user memory, structured output, approvals for risky tools, cost control; agent audit script | "My n8n agent loops and costs too much - review it" |
| [n8n-workflow-docs](skills/n8n-workflow-docs/SKILL.md) | Generates Markdown docs + Mermaid flowcharts (labelled branches, AI sub-nodes) from workflow JSON, one file per workflow plus an index | "Document all our n8n workflows for the client handover" |

## Install

### Claude Code: plugin marketplace (recommended)

```text
/plugin marketplace add saizul/n8n-automation-skills
/plugin install n8n-automation-skills@n8n-automation-skills
```

Or from your shell:

```bash
claude plugin marketplace add saizul/n8n-automation-skills
claude plugin install n8n-automation-skills@n8n-automation-skills
```

Plugin skills are namespaced, e.g. `/n8n-automation-skills:n8n-workflow-lint`. Claude also
picks them up automatically when your request matches a skill's description. Update later
with `/plugin marketplace update n8n-automation-skills`.

### Claude Code: copy the skills

```bash
git clone https://github.com/saizul/n8n-automation-skills.git
# all projects (personal skills)
mkdir -p ~/.claude/skills && cp -r n8n-automation-skills/skills/* ~/.claude/skills/
# or just one project
mkdir -p .claude/skills && cp -r n8n-automation-skills/skills/n8n-workflow-lint .claude/skills/
```

Each release also has one zip per skill and an all-skills zip under
[Releases](https://github.com/saizul/n8n-automation-skills/releases).

### Codex CLI

Codex reads skills from `~/.agents/skills` (all repositories) and `.agents/skills` in a
repository (from the current directory up to the repository root):

```bash
git clone https://github.com/saizul/n8n-automation-skills.git
mkdir -p ~/.agents/skills && cp -r n8n-automation-skills/skills/* ~/.agents/skills/
# or per repository
mkdir -p .agents/skills && cp -r n8n-automation-skills/skills/n8n-workflow-lint .agents/skills/
```

Codex also ships a `$skill-installer` skill that can install skills from a GitHub
repository; ask it to install from `saizul/n8n-automation-skills`. Skill locations and the
installer are described in the [Codex skills documentation](https://developers.openai.com/codex/skills);
check it if your Codex version behaves differently. [AGENTS.md](AGENTS.md) explains how
Codex and other agents should use these skills.

### Any other agent

The skills follow the open [Agent Skills format](https://agentskills.io/specification):
a folder with `SKILL.md` (YAML frontmatter + instructions), `scripts/`, `references/` and
`assets/`. Point your agent at the folder, or run the scripts yourself; every script has `--help`.

## Usage examples

```text
> Here is workflows/order-sync.json. Is it ready for production?
  (Runs lint_workflow.py and check_expressions.py: a hard-coded Bearer token, no error
   workflow, an expression pointing at a renamed node; tells you to rotate the token and
   returns a corrected JSON plus a list of editor changes.)

> Stripe says our n8n webhook signature check fails for every event.
  (webhook_audit.py shows Raw Body is off and the code signs JSON.stringify($json.body);
   swaps in the Stripe snippet with timingSafeEqual and a 5-minute window, then gives you
   a sign_webhook.py command to send a correctly signed test event.)

> Our workers stopped picking up jobs after we moved servers.
  (n8n_env_check.py on `docker compose config --format json` finds the worker has a
   different N8N_ENCRYPTION_KEY hash and no EXECUTIONS_MODE=queue.)
```

Run the scripts directly too:

```bash
python3 skills/n8n-workflow-lint/scripts/lint_workflow.py workflows/ --fail-on high
python3 skills/n8n-expressions-cheatsheet/scripts/check_expressions.py wf.json --list
python3 skills/n8n-error-handling/scripts/error_audit.py workflows/ --json
python3 skills/n8n-webhook-security/scripts/webhook_audit.py workflows/
WEBHOOK_SECRET=... python3 skills/n8n-webhook-security/scripts/sign_webhook.py --scheme github --body payload.json
python3 skills/n8n-self-hosting/scripts/n8n_env_check.py --env .env
python3 skills/n8n-ai-agent-workflows/scripts/agent_audit.py wf.json
python3 skills/n8n-workflow-docs/scripts/workflow_docs.py workflows/ --out docs/workflows
```

Get workflow JSON from the editor (`...` > Download) or on the server with
`n8n export:workflow --all --separate --output=workflows/`.

## How skills work

At startup the agent loads only each skill's `name` and `description` (about 100 tokens
each). When your request matches a description, it reads that skill's `SKILL.md`: the
workflow, decision points and safety rules. Longer material lives in `references/` and is
read only when a step needs it; `assets/` holds importable files (an error workflow, a
compose file). Scripts run as normal commands, so their output is exact and repeatable.

```
skills/<skill-name>/
  SKILL.md          frontmatter (name, description) + workflow
  scripts/          standalone Python 3 helpers (stdlib only, --help, --json)
  references/       patterns, cheatsheets, runbooks
  assets/           files to import or copy (workflow JSON, docker-compose.yml)
```

## FAQ

**Does any script talk to my n8n instance?**
No. Everything works offline on exported JSON, `.env` files or `docker compose config`
output. Nothing is sent anywhere; no telemetry.

**Are my credentials in the export?**
n8n exports reference credentials by id and name only. But anything typed into node
parameters (an `Authorization` header, an API key in a URL, a secret in a Code node) is in
the file. That is exactly what `n8n-workflow-lint` finds, without printing the values.

**n8n Cloud or self-hosted?**
All workflow skills work for both. `n8n-self-hosting` is for self-hosted instances.

**Which n8n versions?**
Written against n8n 1.x and 2.x exports (node `typeVersion`s from 2024-2026). Older
exports mostly work; `executionOrder: v0` and legacy `$node[...]` syntax are reported.

**Can I use the linters in CI?**
Yes: every script exits 1 when findings reach `--fail-on` and supports `--json`.

## Roadmap

Ideas for new skills (great first contributions; see [CONTRIBUTING.md](CONTRIBUTING.md)):

- `n8n-workflow-diff` - semantic diff of two workflow versions (nodes, parameters, connections) for code review
- `n8n-credentials-inventory` - which workflows use which credentials, unused credentials, rotation plan
- `n8n-execution-analyzer` - summarise exported execution data: slowest nodes, error hotspots
- `n8n-migration-from-zapier-make` - map Zaps/Make scenarios to n8n nodes and expressions
- `n8n-custom-node-dev` - scaffold, test and publish a community node
- `n8n-rag-pipelines` - ingestion, chunking, vector stores and evaluation for RAG in n8n
- `n8n-testing` - pinned-data fixtures, test workflows and assertions for regression tests
- `n8n-git-workflows` - export/import scripts and CI for keeping workflows in git

## Contributing

Contributions are welcome: new checks, new skills, fixture workflows from real projects
(redacted), fixes and better docs. Read [CONTRIBUTING.md](CONTRIBUTING.md) for the skill
template, the quality bar and how to run the validator and tests:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python tools/validate_skills.py && ruff check . && ruff format --check . && pytest
```

Please follow the [Code of Conduct](CODE_OF_CONDUCT.md) and report security issues
privately as described in [SECURITY.md](SECURITY.md).

n8n is a trademark of n8n GmbH. This project is independent and not affiliated with n8n.

## License

[MIT](LICENSE) (c) Saizul Amin
