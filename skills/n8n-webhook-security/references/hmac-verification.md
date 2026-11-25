# HMAC verification in an n8n Code node

Setup for every snippet:

- Webhook node: Authentication **None**, HTTP Method POST, Options > **Raw Body** on.
  With Raw Body the exact request bytes are stored as binary property `data`.
- Code node right after it, Mode **Run Once for All Items** (a webhook delivers one item).
- Instance env: `NODE_FUNCTION_ALLOW_BUILTIN=crypto` (set it on the task runner
  container if you run external task runners).
- `SECRET`: load it at runtime from somewhere that is not the workflow JSON, for example
  an earlier node that reads a credential-protected config table or sub-workflow
  (`$('Load signing secret').first().json.secret`). Never paste it into the code.

All snippets throw on failure, which stops the execution (the caller gets an error
response). To answer 401 instead, return `{ valid: false }` and branch with IF into a
Respond to Webhook node.

Header names arrive lower-cased in `$json.headers`.

## Shared helper (paste at the top)

```javascript
const crypto = require('crypto');
const item = $input.first();
// Raw body bytes (works for in-memory and filesystem binary modes)
const raw = await this.helpers.getBinaryDataBuffer(0, 'data');
const headers = item.json.headers || {};
const SECRET = $('Load signing secret').first().json.secret;

function safeEqual(a, b) {
  const x = Buffer.from(String(a));
  const y = Buffer.from(String(b));
  return x.length === y.length && crypto.timingSafeEqual(x, y);
}
function hmacHex(data) {
  return crypto.createHmac('sha256', SECRET).update(data).digest('hex');
}
function fresh(tsSeconds, toleranceSeconds = 300) {
  const ts = Number(tsSeconds);
  return Number.isFinite(ts) && Math.abs(Date.now() / 1000 - ts) <= toleranceSeconds;
}
```

## GitHub

Header `X-Hub-Signature-256: sha256=<hex>` over the raw body. No timestamp: dedupe on
`X-GitHub-Delivery` instead.

```javascript
const sig = headers['x-hub-signature-256'] || '';
if (!safeEqual(sig, 'sha256=' + hmacHex(raw))) throw new Error('Invalid GitHub signature');
return [{ json: { delivery: headers['x-github-delivery'], event: headers['x-github-event'],
                  payload: JSON.parse(raw.toString('utf8')) } }];
```

## Stripe

Header `Stripe-Signature: t=<unix>,v1=<hex>[,v1=...]`, signed string `"<t>.<raw body>"`.

```javascript
const parts = (headers['stripe-signature'] || '').split(',').map(p => p.split('='));
const t = (parts.find(([k]) => k === 't') || [])[1];
const sigs = parts.filter(([k]) => k === 'v1').map(([, v]) => v);
const expected = hmacHex(Buffer.concat([Buffer.from(`${t}.`), raw]));
if (!t || !fresh(t) || !sigs.some(s => safeEqual(s, expected))) {
  throw new Error('Invalid or stale Stripe signature');
}
return [{ json: JSON.parse(raw.toString('utf8')) }];
```

Dedupe on the event `id` (Stripe retries deliveries).

## Shopify

Header `X-Shopify-Hmac-Sha256: <base64>` over the raw body.

```javascript
const sig = headers['x-shopify-hmac-sha256'] || '';
const expected = crypto.createHmac('sha256', SECRET).update(raw).digest('base64');
if (!safeEqual(sig, expected)) throw new Error('Invalid Shopify signature');
const sentAt = Date.parse(headers['x-shopify-triggered-at'] || '');
if (!sentAt || Math.abs(Date.now() - sentAt) > 5 * 60 * 1000) throw new Error('Stale Shopify webhook');
return [{ json: { webhookId: headers['x-shopify-webhook-id'], order: JSON.parse(raw.toString('utf8')) } }];
```

Dedupe on `X-Shopify-Webhook-Id`.

## Slack

Headers `X-Slack-Request-Timestamp` and `X-Slack-Signature: v0=<hex>`, signed string
`"v0:<timestamp>:<raw body>"`. Slack sends form-encoded bodies for slash commands and
interactivity.

```javascript
const ts = headers['x-slack-request-timestamp'];
const sig = headers['x-slack-signature'] || '';
const expected = 'v0=' + hmacHex(Buffer.concat([Buffer.from(`v0:${ts}:`), raw]));
if (!fresh(ts) || !safeEqual(sig, expected)) throw new Error('Invalid or stale Slack signature');
const body = raw.toString('utf8');
const payload = body.startsWith('{') ? JSON.parse(body) : Object.fromEntries(new URLSearchParams(body));
return [{ json: payload }];
```

## Your own callers (generic scheme)

Sign `"<unix timestamp>.<raw body>"` and send `X-Timestamp` and `X-Signature: sha256=<hex>`
(this is what `scripts/sign_webhook.py --scheme generic` produces). Add an `X-Request-Id`
and dedupe on it to block replays inside the window.

```javascript
const ts = headers['x-timestamp'];
const expected = 'sha256=' + hmacHex(Buffer.concat([Buffer.from(`${ts}.`), raw]));
if (!fresh(ts) || !safeEqual(headers['x-signature'] || '', expected)) throw new Error('Invalid signature');
return [{ json: JSON.parse(raw.toString('utf8')) }];
```

For simple server-to-server calls, a Header Auth credential with a long random key is
also fine; HMAC adds integrity and replay protection.

## Without a Code node

The Crypto node (action Hmac, SHA256, hex/base64) can compute an HMAC of a string, followed
by an IF comparing it with the header. This works when the signed string can be rebuilt
exactly; for raw-body signatures the Code node is more reliable. The IF comparison is not
constant-time, and the Crypto node's secret field must not hold the literal secret in
exported workflows.

## Common mistakes

| Mistake | Effect |
| --- | --- |
| Raw Body off, `JSON.stringify($json.body)` | Valid requests fail (key order, spacing, unicode escapes differ) |
| `sig === expected` or `sig.includes(expected)` | Timing leak; `includes` also accepts junk around the value |
| No timestamp check | Captured requests can be replayed forever |
| Secret in the Code node | It is in every export, backup and git commit; rotate it |
| Verification in a later branch | Earlier nodes already acted on unverified data |
| Testing with `/webhook-test/` but publishing `/webhook/` in the provider | Provider calls fail once the editor is closed |
