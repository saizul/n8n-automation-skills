# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-11-25

### Added

- `n8n-workflow-design`: trigger selection, sub-workflow boundaries, idempotency patterns
  (upserts, idempotency keys, processed-events table), batching, pagination, rate limits,
  waits and a plan template.
- `n8n-workflow-lint`: `lint_workflow.py` for default node names, missing error workflow,
  hard-coded credentials and tokens, internal/plain-HTTP URLs, disabled, unused and
  unreachable nodes, dangling connections, duplicate names, long chains, pinned data.
- `n8n-error-handling`: `error_audit.py` (error workflow coverage, swallowed errors,
  unconnected error outputs, missing retries, non-idempotent retries), importable alert
  workflow, dead-letter and replay patterns.
- `n8n-webhook-security`: `webhook_audit.py` (unauthenticated triggers, signature checks
  without Raw Body, non-constant-time compares, replay, literal secrets, public chat) and
  `sign_webhook.py` (GitHub, Stripe, Shopify, Slack, generic HMAC signing and verification);
  Code node verification snippets and proxy rate limiting.
- `n8n-expressions-cheatsheet`: `check_expressions.py` (expression extraction and checks
  for literal braces, Python syntax, statements, missing nodes, dot keys, `$jmespath`
  argument order, `$env`, JS dates and Code node mode mistakes) and a cheatsheet.
- `n8n-self-hosting`: queue-mode `docker-compose.yml` and `.env.example`,
  `n8n_env_check.py` for `.env` and `docker compose config` output, backup/restore and
  upgrade guides including n8n 2.0 breaking changes.
- `n8n-ai-agent-workflows`: `agent_audit.py` (model, memory, tools, iterations, session
  keys, output parsers, side-effect tools, token limits) and agent/cost-control patterns.
- `n8n-workflow-docs`: `workflow_docs.py` generating Markdown and Mermaid diagrams per
  workflow plus an index.
- Claude Code plugin marketplace manifest, `tools/validate_skills.py`, CI and release workflows.

[Unreleased]: https://github.com/saizul/n8n-automation-skills/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/saizul/n8n-automation-skills/releases/tag/v0.1.0
