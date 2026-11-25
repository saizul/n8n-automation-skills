# Rate limiting and exposure

n8n does not rate-limit webhook calls per client. Do it in front of n8n, and expose as
little as possible.

## nginx

```nginx
# http {} context
limit_req_zone $binary_remote_addr zone=n8n_webhook:10m rate=10r/s;
limit_req_zone $binary_remote_addr zone=n8n_chat:10m rate=20r/m;
limit_req_status 429;

server {
    listen 443 ssl;
    http2 on;
    server_name n8n.example.com;
    client_max_body_size 16m;          # n8n default payload limit is 16 MB (N8N_PAYLOAD_SIZE_MAX)

    # Public: production webhooks, forms
    location /webhook/ {
        limit_req zone=n8n_webhook burst=20 nodelay;
        proxy_pass http://127.0.0.1:5678;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
    }
    location /form/ {
        limit_req zone=n8n_webhook burst=10 nodelay;
        proxy_pass http://127.0.0.1:5678;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Editor, REST API, test webhooks: only from the office/VPN
    location / {
        allow 203.0.113.0/24;          # replace with your office / VPN range
        deny all;
        proxy_pass http://127.0.0.1:5678;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;        # editor push connection
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Paths: production webhooks `/webhook/`, test webhooks `/webhook-test/`, waiting
executions `/webhook-waiting/`, forms `/form/` (and `/form-test/`, `/form-waiting/`). The
prefixes can be changed with `N8N_ENDPOINT_WEBHOOK` and related variables; keep the proxy
in sync. Chat Trigger public URLs are served under `/webhook/` too; give them their own
`location` with the `n8n_chat` zone if you know the path.

Set `N8N_PROXY_HOPS=1` so n8n reads the client IP from `X-Forwarded-For` (needed for the
Webhook node's IP(s) Allowlist behind a proxy).

## Caddy

Caddy has no built-in rate limiter; use the `caddy-ratelimit` plugin or put Cloudflare in
front. Access restriction without plugins:

```caddyfile
n8n.example.com {
    @editor not path /webhook/* /form/*
    handle @editor {
        @blocked not remote_ip 203.0.113.0/24
        respond @blocked 403
        reverse_proxy 127.0.0.1:5678
    }
    handle {
        request_body {
            max_size 16MB
        }
        reverse_proxy 127.0.0.1:5678
    }
}
```

## Cloudflare (if the site is proxied)

- WAF rate limiting rule: path starts with `/webhook/`, e.g. 100 requests per 10 seconds per IP.
- Restrict the editor with Cloudflare Access (Zero Trust) and leave `/webhook/*` bypassed.

## Other controls

- Webhook node IP(s) Allowlist when the provider publishes source ranges (GitHub `meta`
  API, Stripe, Shopify docs). Keep it in addition to signatures, not instead.
- Ignore Bots option for webhooks that link previews may hit.
- Respond quickly and process asynchronously so slow downstream APIs do not hold proxy
  connections open.
- Chat bots: limit messages per session in the workflow (count in memory/DB) and cap
  model tokens; see `n8n-ai-agent-workflows`.
