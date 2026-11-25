---
name: n8n-webhook-security
description: Secure n8n Webhook, Form Trigger and Chat Trigger endpoints - pick the right authentication (Header Auth, Basic, JWT, IP allowlist), verify provider HMAC signatures (GitHub, Stripe, Shopify, Slack, generic) in a Code node over the raw body with timingSafeEqual and replay windows, respond safely, and rate-limit /webhook/ at the reverse proxy. Includes an audit script for exported workflows (unauthenticated webhooks, signature checks without Raw Body, non-constant-time compares, secrets typed into nodes, public chat bots) and a signer to send correctly signed test requests. Use when the user exposes an n8n webhook to the internet, integrates Stripe/GitHub/Shopify/Slack webhooks, asks how to protect or authenticate a webhook, sees fake or replayed requests, or publishes a chat/form trigger.
license: MIT
compatibility: Python 3.8+ standard library only. sign_webhook.py reads the secret from an environment variable; no network access.
metadata:
  author: Saizul Amin
  version: "0.1.0"
---

# n8n webhook security

A production webhook URL is public. Treat every request as hostile until the workflow has
proved who sent it. Never put the secret in the workflow JSON or the chat.

Paths below are relative to this skill's folder (in Claude Code: `${CLAUDE_SKILL_DIR}`).

## When to use

- A workflow starts with a Webhook, Form Trigger or Chat Trigger that is reachable from the internet
- Receiving Stripe, GitHub, Shopify, Slack, Twilio, Calendly... webhooks
- "How do I secure my n8n webhook?", "someone is calling my webhook", "signature does not match"
- Before publishing a chat bot or form

## When not to use

- n8n editor login, SSO, 2FA, TLS certificates: `n8n-self-hosting` (and your proxy docs)
- General workflow review: `n8n-workflow-lint`
- Calling other APIs securely from n8n: use credentials (lint flags hard-coded tokens)

## Workflow

1. **Inventory the endpoints** (read-only):
   ```bash
   python3 scripts/webhook_audit.py workflows/
   python3 scripts/webhook_audit.py wf.json --json --fail-on high
   ```
   It lists every Webhook/Form/Chat trigger with method, path and auth, then findings.
2. **Choose the protection** per caller:

   | Caller | Use |
   | --- | --- |
   | Provider that signs requests (Stripe, GitHub, Shopify, Slack) | Authentication None + Raw Body on + HMAC check in a Code node right after the webhook |
   | Your own app / script / another n8n | Header Auth credential (e.g. `X-Api-Key`, 32+ random bytes) or JWT Auth |
   | Provider that can only do Basic auth or a static header | Basic Auth / Header Auth + IP(s) Allowlist with the provider's published ranges |
   | Browser forms | Form Trigger; bot protection and rate limits; no secrets in the page |
   | Chat bots | Chat Trigger with authentication, or embed behind your own logged-in app |

   The webhook path is not a secret (it appears in logs, browser history, provider
   dashboards). A random path helps only against scanners.
3. **Implement signature verification** with the snippet for the provider from
   [references/hmac-verification.md](references/hmac-verification.md):
   - Webhook node > Options > **Raw Body** on (signatures are over the exact bytes;
     `JSON.stringify($json.body)` is not the same bytes).
   - Code node directly after the webhook: compute HMAC over the raw body, compare with
     `crypto.timingSafeEqual`, check the timestamp window (5 minutes), throw on failure.
   - The Code node needs `NODE_FUNCTION_ALLOW_BUILTIN=crypto` on the instance (on the
     task runner when runners are external). Ask the user to set it; do not change server
     env yourself.
   - Load the signing secret from a credential-backed source (for example a sub-workflow
     or a config table), not a literal in the code.
4. **Respond safely.** Use "Respond: Using 'Respond to Webhook' node" or "Immediately";
   return `202` with no internal data. On a failed check, the thrown error makes n8n
   answer 500; if the provider retries on 5xx, prefer an IF + Respond to Webhook 401.
5. **Rate-limit at the proxy.** n8n has no per-IP rate limit for webhooks. Add the nginx
   or Caddy rules from [references/rate-limiting.md](references/rate-limiting.md), and keep
   the editor (`/`, `/rest/`) behind VPN/SSO/IP allowlist if possible while `/webhook/`
   stays public.
6. **Test with signed requests** (the user supplies the secret through an env var; it is
   never printed):
   ```bash
   export WEBHOOK_SECRET='...'     # the user types this, not the agent
   python3 scripts/sign_webhook.py --scheme stripe --body payload.json \
       --curl https://n8n.example.com/webhook-test/stripe
   python3 scripts/sign_webhook.py --scheme github --body payload.json --verify 'sha256=...'
   ```
   Test a valid request, a tampered body (must fail), and an old timestamp (must fail).
   Use the `/webhook-test/` URL while the workflow is open in the editor, `/webhook/` once published.
7. **Re-run the audit** and confirm no high/medium findings remain.

## Output

```
Endpoints (3)
  POST /webhook/stripe   auth=none  -> HMAC check present, Raw Body OFF   (MEDIUM)
  POST /webhook/orders   auth=none  -> no verification                    (HIGH)
  chat  public           auth=none  -> anyone can run the agent           (MEDIUM)

Fixes
1. orders: add Header Auth credential "ERP webhook key"; ERP sends X-Api-Key
2. stripe: Raw Body on; replace Check signature with the Stripe snippet (timingSafeEqual,
   5 min tolerance); move the whsec_ secret out of the code and rotate it in Stripe
3. chat: enable Basic Auth or embed in the logged-in portal; nginx limit_req 10r/m per IP
Tests: valid signature 200, tampered body 401, replayed (10 min old) 401
```
