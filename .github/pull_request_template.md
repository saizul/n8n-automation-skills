## What does this pull request do?

<!-- One or two sentences. Link the issue: "Closes #123". -->

## Type

- [ ] New skill
- [ ] New check or option in an existing script
- [ ] Workflow / reference improvement
- [ ] Bug fix
- [ ] Repository tooling (validator, CI)

## Checklist

- [ ] `python tools/validate_skills.py` passes
- [ ] `ruff check . && ruff format --check .` passes
- [ ] `pytest` passes; new script behaviour has tests with fixtures in `tests/fixtures/`
- [ ] Scripts use only the Python standard library, support `--help`, have a `--json`
      option where output is structured, and exit non-zero on findings
- [ ] Scripts are read-only, make no network calls and never print secret values
- [ ] Fixtures contain no real secrets, customer data or real hostnames (use example.com and
      fake values built so secret scanners do not match them)
- [ ] New skills: listed in `.claude-plugin/marketplace.json`, the README skills table,
      `AGENTS.md` and CHANGELOG.md
