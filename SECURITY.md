# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 0.1.x | yes |

## What counts as a vulnerability

These skills are run by AI agents on real automation projects, so unsafe guidance is
treated like a code vulnerability:

- A script that prints a secret value (header or query credentials, tokens, webhook
  signing secrets, `N8N_ENCRYPTION_KEY`, database passwords) instead of only its location
- A script that writes outside the output directory it was given, makes network calls, or
  talks to an n8n instance
- Command or code injection: crafted workflow JSON, `.env` or compose files that make a
  script execute something
- A `SKILL.md` workflow that tells the agent to publish workflows, change server settings,
  delete data or use decrypted credential exports without asking the user first
- Security advice that is wrong in a way that leaves users exposed, e.g. a signature
  verification snippet that accepts forged requests

A check that misses a problem (false negative) is a normal bug: please open an issue.

## Reporting

Please **do not** open a public issue. Use
[GitHub private vulnerability reporting](https://github.com/saizul/n8n-automation-skills/security/advisories/new)
with the affected skill, version, impact and steps to reproduce. Remove real workflow
data, hostnames and secrets from examples.

You will get an answer within 5 working days. Fixes are released as soon as possible and
credited in the changelog unless you prefer otherwise.

Vulnerabilities in n8n itself should go to the n8n project, not here.
