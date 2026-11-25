# Contributing to n8n-automation-skills

Thank you for helping! New checks, new skills, fixes, fixture workflows from real projects
(redacted) and clearer docs are all welcome.

- [Ways to contribute](#ways-to-contribute)
- [Good first issues](#good-first-issues)
- [Development setup](#development-setup)
- [Repository layout](#repository-layout)
- [Write a new skill](#write-a-new-skill)
- [Quality bar](#quality-bar)
- [Pull request process](#pull-request-process)

## Ways to contribute

| Size | Idea |
| --- | --- |
| 10 minutes | Fix a typo, clarify a workflow step, add an expression example to the cheatsheet |
| 30 minutes | Add a fixture workflow that triggers a false positive or a missed finding |
| 1-2 hours | Add a check to an existing script (see below) |
| Half a day | Write a new skill from the template |

Comment on an issue before you start so nobody else picks the same one.

## Good first issues

Each of these is self-contained and touches one script and its test file.

1. **n8n-workflow-lint: `executeOnce` on a node fed by many items.** Report `info` when a
   node with `executeOnce: true` sits right after a node that usually returns many items
   (HTTP Request with pagination, database queries), since only the first item is used.
2. **n8n-workflow-lint: sticky-note TODOs.** Report `info` for sticky notes that contain
   `TODO` or `FIXME` so handovers do not miss them.
3. **n8n-workflow-lint: `--baseline` file.** Accept a JSON file of accepted findings
   (check + workflow + node) and report only new ones, for gradual adoption in CI.
4. **n8n-expressions-cheatsheet: `.item` after aggregation.** Flag `$('X').item` when `X`
   is an Aggregate or Summarize node (one item out, so `.first()` is clearer).
5. **n8n-expressions-cheatsheet: Luxon format tokens.** Flag `toFormat('YYYY-MM-DD')`
   (Moment tokens) and suggest `yyyy-LL-dd`.
6. **n8n-error-handling: alert workflow loops.** Detect an error workflow whose own
   `settings.errorWorkflow` points back into a cycle across several workflows.
7. **n8n-webhook-security: IP allowlist awareness.** Treat a non-empty `ipWhitelist`
   option as partial protection (downgrade `webhook.no_auth` to medium) and test it.
8. **n8n-ai-agent-workflows: `$fromAI()` without description.** In app "... Tool" nodes,
   report `$fromAI('field')` calls that pass no description argument.
9. **n8n-self-hosting: webhook processor checks.** When a `webhook` service exists, check
   `N8N_DISABLE_PRODUCTION_MAIN_PROCESS` and that main and webhook use the same URL vars.
10. **n8n-workflow-docs: `--diagram-direction TB`.** Option for top-to-bottom diagrams, and
    collapse AI sub-nodes into the agent label with `--compact`.
11. **n8n-workflow-docs: expressions section.** List which nodes each node references via
    `$('Name')`, as a "Data dependencies" table.
12. **New skill: `n8n-workflow-diff`** (see the roadmap in the README).

## Development setup

```bash
git clone https://github.com/saizul/n8n-automation-skills.git
cd n8n-automation-skills
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt

python tools/validate_skills.py   # SKILL.md frontmatter, sections, links, assets, marketplace listing
ruff check . && ruff format --check .
pytest
```

PyYAML is optional; without it the compose-YAML test is skipped.

To try your changes in Claude Code, either copy a skill to `~/.claude/skills/` or add your
checkout as a local marketplace:

```bash
claude plugin marketplace add ./n8n-automation-skills
claude plugin install n8n-automation-skills@n8n-automation-skills
```

For Codex, copy the skill folder to `~/.agents/skills/`.

## Repository layout

```
.claude-plugin/
  marketplace.json      marketplace + plugin entry listing every skill
  plugin.json           plugin manifest (name, version, author)
skills/<name>/
  SKILL.md              frontmatter + workflow (the part agents read)
  scripts/*.py          standalone helpers, Python 3.8+ stdlib only
  references/*.md       detail loaded on demand
  assets/               importable workflow JSON, compose files
templates/skill/        copy this to start a new skill
tests/
  test_<skill>.py       one test module per skill
  fixtures/workflows/   shared, realistic workflow exports (good and bad)
  fixtures/<skill>/     skill-specific inputs (.env, compose JSON)
tools/validate_skills.py
```

## Write a new skill

1. Open a "New skill proposal" issue first so we can agree on scope.
2. Copy the template:
   ```bash
   cp -r templates/skill skills/n8n-my-skill
   mv skills/n8n-my-skill/scripts/your_script.py skills/n8n-my-skill/scripts/my_check.py
   ```
3. Edit `SKILL.md`:
   - `name` equals the folder name: lowercase letters, digits, single hyphens, max 64 chars.
   - `description` (max 1024 chars) says **what** it does with concrete keywords and **when**
     to use it, starting the second part with "Use when ...". This text decides whether
     the agent picks the skill, so list the phrases and error messages users actually type.
   - Required sections: `## When to use`, `## When not to use`, `## Workflow`, `## Output`.
   - Workflow steps are imperative and numbered, with exact commands, decision points and
     the point where the agent must ask before changing anything.
   - Keep it under 400 lines; move tables, snippets and long configs to `references/`.
4. Write the script: `argparse` with a useful `--help`, `--json` output, exit codes
   0 (ok) / 1 (findings) / 2 (error), read-only, no network, never print secret values.
   Accept the three export shapes (single workflow, array, `{"data": [...]}`) and folders;
   copy the small loader from an existing script.
5. Add `tests/test_n8n_my_skill.py`. Reuse `tests/fixtures/workflows/` where possible and
   add new fixtures for your cases. Cover a bad input, a good input with no findings, edge
   cases and the CLI exit codes.
6. Add the skill to `.claude-plugin/marketplace.json` (`plugins[0].skills`), the README
   skills table, the table in `AGENTS.md`, the bug report dropdown and `CHANGELOG.md`
   under "Unreleased".
7. Run the validator, ruff and pytest.

## Quality bar

- **Grounded in real n8n.** Parameter names and node types must match real exports. When
  you add a check, add a fixture made from a real (redacted) export that shows it.
- **Safety first.** A skill must never tell the agent to publish workflows, call the n8n
  API or change server settings without showing the change and getting confirmation.
- **Deterministic where it matters.** If a step is "parse this JSON and find X", write a
  script and test it instead of relying on the model reading a 3,000-line export.
- **Low false positives.** If a check is often wrong, make it `info` or drop it, and
  document known false positives in the skill's references.
- **Fake secrets only.** Build test tokens from pieces in test code so secret scanners do
  not match the repository.

## Pull request process

1. Fork, create a branch, make focused commits.
2. Make sure CI passes locally (validator, ruff, pytest).
3. Fill in the pull request template.
4. A maintainer reviews within a few days. Small, focused pull requests are merged fastest.

By contributing you agree that your contributions are licensed under the MIT License.
