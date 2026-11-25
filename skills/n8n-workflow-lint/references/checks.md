# Lint checks

| Check | Severity | Why it matters | Fix |
| --- | --- | --- | --- |
| `credentials.hardcoded` | high | Secrets in parameters end up in exports, backups, git, execution data and screenshots | Rotate; use a credential (Header Auth, Query Auth, Bearer Auth, or the service's predefined type) |
| `connections.dangling` | high | A connection names a node that no longer exists (edited JSON, bad merge) | Reconnect in the editor or fix the name in `connections` |
| `node.duplicate_name` | high | n8n requires unique names; imports misbehave | Rename one node |
| `settings.error_workflow` | medium (info for manual-only) | Production failures are visible only in the executions list | Set Workflow settings > Error workflow |
| `url.internal` | medium | `localhost`, RFC 1918 IPs, `.local` and bare container names break when moved between environments | Base URL in a credential, `$vars`, or a config sub-workflow |
| `url.plain_http` | medium | Traffic, tokens and personal data in clear text | Use HTTPS |
| `node.unused` | low | Not connected to anything: dead weight, confuses readers | Delete or connect |
| `node.unreachable` | low | Connected, but no trigger leads to it | Connect to the flow or delete |
| `node.disabled` | low | Disabled nodes pass data through or stop branches silently | Delete, or re-enable and test |
| `node.default_name` | low (info for triggers) | "HTTP Request1" says nothing in logs, error alerts and expressions | Rename to what it does |
| `pindata.present` | low | Pinned data is exported and may contain personal data; pinned nodes do not run in manual tests | Unpin before exporting/committing |
| `chain.long` | low | Long linear chains are hard to test and reuse | Extract sub-workflows |
| `workflow.large` | low | Big workflows load slowly and use more memory per execution | Split by trigger / responsibility |
| `settings.execution_order` | info | `v0` (legacy) runs branches breadth-first; new workflows use `v1` | Switch to v1 and re-test multi-branch flows |
| `url.hardcoded` | info | Fine for public APIs; noisy for per-environment hosts | Optional: credential base URL / `$vars` |
| `code.long` | info | Logic hidden in one Code node is hard to review | Split or move to a sub-workflow |

## How secrets are detected

- `{name, value}` pairs (headers, query, body parameters) named `Authorization`,
  `X-API-Key`, `api_key`, `token`, `secret`, `password`, ... with a fixed value.
- Parameter keys ending in `password`, `secret`, `apiKey`, `accessToken`, `privateKey`
  with a fixed value (for example the Crypto node's HMAC `secret`).
- Known token shapes anywhere in parameters, including Code: bearer tokens, `sk-...`,
  Stripe `sk_live_`/`whsec_`, GitHub `ghp_`, Slack `xox?-`, AWS `AKIA...`, JWTs and
  `user:password@` in URLs.
- In Code nodes: `secret = '...'`, `apiKey: "..."` style literals of 8+ characters.

Expressions (`=...{{ }}`) that compute a value are not reported, unless they wrap a long
literal (`={{ 'Bearer abc...' }}`).

## Known false positives

- Public, non-secret "keys" (e.g. a publishable Stripe key `pk_...` is not matched; a
  Google Maps browser key in a header would be). Use `--ignore credentials.hardcoded` only
  after checking.
- Default-name detection compares the name with the node type; a node you deliberately
  called "Webhook" is reported as info (triggers) or low.
- Sub-workflows with only an Execute Workflow Trigger do not need their own error
  workflow when the parent waits for them; failures surface in the parent.

## Export formats accepted

- A single workflow object (editor download, `export:workflow --id`)
- An array of workflows (`export:workflow --all` without `--separate`)
- `{"data": [...]}` from `GET /api/v1/workflows`
- A directory: every `*.json` inside, recursively
