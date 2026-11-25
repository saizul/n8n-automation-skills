---
name: n8n-workflow-lint
description: Lint exported n8n workflow JSON with a deterministic script - default node names ("HTTP Request1", "Code"), missing error workflow, hard-coded API keys, tokens and Authorization headers (values never printed), hard-coded/internal/plain-HTTP URLs, disabled, unused and unreachable nodes, connections to renamed nodes, duplicate names, overly long chains, huge workflows, pinned data left in exports and legacy execution order. Works on single files, `n8n export:workflow --all` output and folders, with JSON output and CI exit codes. Use when the user shares a workflow .json, asks to review, clean up or "productionize" an n8n workflow, wants a pre-commit/CI check for workflows in git, or before activating or handing over a workflow.
license: MIT
compatibility: Python 3.8+ standard library only. Works offline on exported JSON; never connects to n8n.
metadata:
  author: Saizul Amin
  version: "0.1.0"
---

# n8n workflow lint

Static, read-only review of exported workflows. The script finds the mechanical problems;
you add judgement about design and the business logic.

Paths below are relative to this skill's folder (in Claude Code: `${CLAUDE_SKILL_DIR}`).

## When to use

- The user pastes or attaches an n8n workflow JSON (has `"nodes"` and `"connections"`)
- "Review my workflow", "is this ready for production?", "clean up this workflow"
- Workflows are kept in git and need a CI gate
- Before activating/publishing a workflow or handing it to a client

## When not to use

- Expression syntax problems in detail: `n8n-expressions-cheatsheet` (its script checks every `{{ }}`)
- Webhook authentication and signatures: `n8n-webhook-security`
- Retry/error-workflow coverage across many workflows: `n8n-error-handling`
- Live instance problems (queue stuck, DB growing): `n8n-self-hosting`

## Workflow

1. **Get the JSON.** Any of:
   - Editor: open the workflow > `...` menu > Download (or select all, copy, paste into a file)
   - CLI on the server: `n8n export:workflow --id=<ID> --output=wf.json`, or all workflows:
     `n8n export:workflow --all --separate --output=workflows/`
     (in Docker: `docker compose exec n8n n8n export:workflow ...`)
   - Public API: `GET /api/v1/workflows/{id}` with an API key (the user runs it)
   Exports contain no credential secrets, but may contain pinned data and anything typed
   into parameters. Do not paste a whole export with real data into chat if avoidable;
   run the script on the file.
2. **Run the linter (read-only).**
   ```bash
   python3 scripts/lint_workflow.py wf.json
   python3 scripts/lint_workflow.py workflows/ --json
   python3 scripts/lint_workflow.py workflows/ --fail-on high --ignore node.default_name
   python3 scripts/lint_workflow.py wf.json --max-chain 15 --max-nodes 40
   ```
   Exit 1 = findings at or above `--fail-on` (default `medium`), 2 = unreadable input.
   Secret values are never printed, only the parameter path.
3. **Triage in this order** (meaning and fixes for each check in
   [references/checks.md](references/checks.md)):
   1. `credentials.hardcoded`: tell the user to rotate the secret now (it is in every
      export, backup and git commit) and move it into a credential.
   2. `connections.dangling`, `node.duplicate_name`: the workflow is broken or will break.
   3. `settings.error_workflow`: failures are silent; set up alerts (`n8n-error-handling`).
   4. `url.internal`, `url.plain_http`: environment coupling and plaintext traffic.
   5. Hygiene: unused/unreachable/disabled nodes, default names, pinned data, long chains.
4. **Read the workflow yourself** for what a linter cannot see: duplicate side effects on
   retry, missing dedupe, IF branches that drop items, `Execute Once` misuse, Merge mode.
   Use the design rules in `n8n-workflow-design` if installed.
5. **Propose fixes as a list or a corrected JSON.** For JSON edits, keep node `id`s and
   positions, rename references in expressions when renaming nodes (n8n only rewrites
   references for renames done in the editor), and never invent credential ids. The user
   imports the result; do not call the n8n API yourself unless asked.
6. **Re-run the linter** on the fixed file and show the before/after counts.

## CI usage

```yaml
# .github/workflows/n8n-lint.yml (in the repo that stores exported workflows)
- run: python3 path/to/lint_workflow.py workflows/ --fail-on medium
```

## Output

```
n8n lint: "Order sync (legacy)" - 3 high, 2 medium, 6 low, 2 info

HIGH  HTTP Request1: Authorization header hard-coded (headerParameters.parameters[0])
      -> rotate the token, create a Header Auth credential, select it in the node
HIGH  Send to CRM: api_key query parameter hard-coded -> Query Auth credential
MED   no error workflow -> set "Alerts" in Workflow settings
MED   HTTP Request1: internal URL http://10.0.0.12:8080 -> base URL in $vars / credential
LOW   unused node "Debug"; disabled node "Old Mapper"; default names: Code, If, HTTP Request1
      -> suggested names: "Extract order fields", "Paid over 100?", "Create order in ERP"

Next: corrected JSON attached / changes to make in the editor (numbered).
```
