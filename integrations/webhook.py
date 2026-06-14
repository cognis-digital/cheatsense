#!/usr/bin/env python3
"""Minimal, dependency-free webhook forwarder for Cognis findings.

Reads JSON findings on stdin and POSTs them to a URL (SIEM/Slack/Jira bridge).
Usage:  <tool> scan . --format json | python integrations/webhook.py --url URL
"""
from __future__ import annotations
import argparse
import sys
import urllib.request
import urllib.parse


def main() -> int:
    ap = argparse.ArgumentParser(
        description="POST cheatsense JSON findings to a webhook URL.",
    )
    ap.add_argument("--url", required=True, help="Destination URL (http/https)")
    ap.add_argument("--header", action="append", default=[], metavar="KEY: VALUE",
                    help="Extra request header in 'Key: Value' form (repeatable)")
    args = ap.parse_args()

    # Validate URL scheme to avoid unintended file:// or other protocols.
    parsed = urllib.parse.urlparse(args.url)
    if parsed.scheme not in ("http", "https"):
        print(
            f"webhook: error: URL scheme must be http or https, got {parsed.scheme!r}",
            file=sys.stderr,
        )
        return 2

    # Validate header format before sending anything.
    parsed_headers: list[tuple[str, str]] = []
    for raw_header in args.header:
        if ":" not in raw_header:
            print(
                "webhook: error: --header must be in 'Key: Value' form,"
                f" got: {raw_header!r}",
                file=sys.stderr,
            )
            return 2
        k, _, v = raw_header.partition(":")
        k, v = k.strip(), v.strip()
        if not k:
            print(
                f"webhook: error: header name is empty in: {raw_header!r}",
                file=sys.stderr,
            )
            return 2
        parsed_headers.append((k, v))

    payload = sys.stdin.read()
    if not payload.strip():
        print("webhook: error: stdin is empty — nothing to send", file=sys.stderr)
        return 2
    encoded = payload.encode("utf-8")

    req = urllib.request.Request(args.url, data=encoded, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in parsed_headers:
        req.add_header(k, v)

    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            print(f"posted {len(encoded)} bytes -> {r.status}")
        return 0
    except Exception as e:
        print(f"webhook error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
