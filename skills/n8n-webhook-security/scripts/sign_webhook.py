#!/usr/bin/env python3
"""Sign or verify a webhook payload the way common providers do, for testing n8n.

Schemes:
  github   X-Hub-Signature-256: sha256=HEX(HMAC_SHA256(secret, body))
  stripe   Stripe-Signature: t=TS,v1=HEX(HMAC_SHA256(secret, "TS.body"))
  shopify  X-Shopify-Hmac-Sha256: BASE64(HMAC_SHA256(secret, body))
  slack    X-Slack-Request-Timestamp: TS
           X-Slack-Signature: v0=HEX(HMAC_SHA256(secret, "v0:TS:body"))
  generic  X-Timestamp: TS
           X-Signature: sha256=HEX(HMAC_SHA256(secret, "TS.body"))

The secret is read from an environment variable (default WEBHOOK_SECRET) and is
never printed. The body is used byte for byte, exactly as it will be sent.

Examples:
  WEBHOOK_SECRET=... sign_webhook.py --scheme github --body payload.json
  WEBHOOK_SECRET=... sign_webhook.py --scheme stripe --body payload.json \\
      --curl https://n8n.example.com/webhook/stripe
  WEBHOOK_SECRET=... sign_webhook.py --scheme shopify --body payload.json --verify "BASE64..."

Exit codes: 0 = signed / signature valid, 1 = signature invalid, 2 = error.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import shlex
import sys
import time

SCHEMES = ("github", "stripe", "shopify", "slack", "generic")


def _hmac(secret: bytes, msg: bytes) -> bytes:
    return hmac.new(secret, msg, hashlib.sha256).digest()


def sign(scheme: str, secret: bytes, body: bytes, ts: int) -> dict[str, str]:
    t = str(ts).encode()
    if scheme == "github":
        return {"X-Hub-Signature-256": "sha256=" + _hmac(secret, body).hex()}
    if scheme == "stripe":
        return {"Stripe-Signature": f"t={ts},v1=" + _hmac(secret, t + b"." + body).hex()}
    if scheme == "shopify":
        return {"X-Shopify-Hmac-Sha256": base64.b64encode(_hmac(secret, body)).decode()}
    if scheme == "slack":
        return {
            "X-Slack-Request-Timestamp": str(ts),
            "X-Slack-Signature": "v0=" + _hmac(secret, b"v0:" + t + b":" + body).hex(),
        }
    if scheme == "generic":
        return {"X-Timestamp": str(ts), "X-Signature": "sha256=" + _hmac(secret, t + b"." + body).hex()}
    raise ValueError(f"unknown scheme {scheme}")


def verify(scheme: str, secret: bytes, body: bytes, signature: str, ts: int | None, tolerance: int) -> tuple[bool, str]:
    """Return (valid, reason)."""
    now = int(time.time())
    if scheme == "stripe":
        parts = dict(p.split("=", 1) for p in signature.split(",") if "=" in p)
        if "t" not in parts:
            return False, "no t= in Stripe-Signature"
        ts = int(parts["t"])
        candidates = [v for k, v in (p.split("=", 1) for p in signature.split(",") if "=" in p) if k == "v1"]
        expected = sign(scheme, secret, body, ts)["Stripe-Signature"].split("v1=", 1)[1]
        ok = any(hmac.compare_digest(c, expected) for c in candidates)
    else:
        if scheme in ("slack", "generic") and ts is None:
            return False, "--timestamp is required for this scheme"
        headers = sign(scheme, secret, body, ts or 0)
        expected = (
            headers.get("X-Hub-Signature-256")
            or headers.get("X-Shopify-Hmac-Sha256")
            or headers.get("X-Slack-Signature")
            or headers.get("X-Signature")
        )
        ok = hmac.compare_digest(signature.strip(), expected)
    if not ok:
        return False, "signature mismatch"
    if ts is not None and scheme in ("stripe", "slack", "generic") and tolerance and abs(now - ts) > tolerance:
        return False, f"timestamp outside the {tolerance}s window (possible replay)"
    return True, "valid"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Sign or verify webhook payloads (GitHub, Stripe, Shopify, Slack, generic HMAC).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:", 1)[1],
    )
    p.add_argument("--scheme", required=True, choices=SCHEMES)
    p.add_argument("--body", required=True, help="file with the exact request body, or - for stdin")
    p.add_argument("--secret-env", default="WEBHOOK_SECRET", help="env var holding the secret")
    p.add_argument("--timestamp", type=int, help="unix timestamp to sign with / verify against (default now)")
    p.add_argument("--verify", metavar="SIGNATURE", help="verify this header value instead of signing")
    p.add_argument("--tolerance", type=int, default=300, help="replay window in seconds when verifying (0 = off)")
    p.add_argument("--curl", metavar="URL", help="print a ready-to-run curl command for this URL")
    p.add_argument("--json", action="store_true", help="print JSON")
    args = p.parse_args(argv)

    if args.curl and args.body == "-":
        print("error: --curl needs --body FILE (curl must re-read the same bytes)", file=sys.stderr)
        return 2
    secret = os.environ.get(args.secret_env)
    if not secret:
        print(f"error: set the secret in ${args.secret_env} (it is never printed)", file=sys.stderr)
        return 2
    try:
        if args.body == "-":
            body = sys.stdin.buffer.read()
        else:
            with open(args.body, "rb") as fh:
                body = fh.read()
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.verify is not None:
        ok, reason = verify(args.scheme, secret.encode(), body, args.verify, args.timestamp, args.tolerance)
        if args.json:
            print(json.dumps({"valid": ok, "reason": reason}))
        else:
            print(("VALID" if ok else "INVALID") + f": {reason}")
        return 0 if ok else 1

    ts = args.timestamp if args.timestamp is not None else int(time.time())
    headers = sign(args.scheme, secret.encode(), body, ts)
    if args.json:
        print(json.dumps({"scheme": args.scheme, "timestamp": ts, "headers": headers}, indent=2))
    elif args.curl:
        hdrs = " ".join(f"-H {shlex.quote(f'{k}: {v}')}" for k, v in headers.items())
        src = "@" + args.body
        print(
            f"curl -sS -X POST {shlex.quote(args.curl)} -H 'Content-Type: application/json' {hdrs} "
            f"--data-binary {shlex.quote(src)}"
        )
    else:
        for k, v in headers.items():
            print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
