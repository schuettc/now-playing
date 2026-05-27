#!/usr/bin/env python3
"""One-time Last.fm session-key bootstrap.

Run on any machine with a browser. You need:
    LASTFM_API_KEY     — set in env or via --api-key
    LASTFM_API_SECRET  — set in env or via --api-secret

Both can be obtained at https://www.last.fm/api/account/create.

Usage:

    LASTFM_API_KEY=xxx LASTFM_API_SECRET=yyy \\
        python3 pi/scripts/lastfm_auth.py

The script:
    1. Calls auth.getToken to obtain a request token.
    2. Prints an authorization URL — open it, log in, and click "Yes, allow".
    3. Waits for you to press Enter.
    4. Calls auth.getSession with the signed token to get a permanent
       session key.
    5. Prints the session key. Paste it into pi/.env as
       LASTFM_SESSION_KEY=<value>.

The session key never expires unless the user revokes it from
https://www.last.fm/settings/applications.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request

API_URL = "https://ws.audioscrobbler.com/2.0/"


def _sign(params: dict[str, str], secret: str) -> str:
    parts = []
    for k in sorted(params.keys()):
        if k in ("format", "callback"):
            continue
        parts.append(f"{k}{params[k]}")
    parts.append(secret)
    return hashlib.md5("".join(parts).encode("utf-8")).hexdigest()


def _call(method: str, api_key: str, *, secret: str | None = None,
          extra: dict[str, str] | None = None, signed: bool = False) -> dict:
    params: dict[str, str] = {"method": method, "api_key": api_key}
    if extra:
        params.update(extra)
    if signed:
        assert secret is not None
        params["api_sig"] = _sign(params, secret)
    params["format"] = "json"
    url = API_URL + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=15) as resp:
        body = resp.read().decode("utf-8")
    data = json.loads(body)
    if "error" in data:
        raise SystemExit(
            f"Last.fm error {data['error']}: {data.get('message')}"
        )
    return data


def main() -> int:
    p = argparse.ArgumentParser(description="Bootstrap a Last.fm session key.")
    p.add_argument("--api-key", default=os.environ.get("LASTFM_API_KEY", "").strip())
    p.add_argument("--api-secret", default=os.environ.get("LASTFM_API_SECRET", "").strip())
    args = p.parse_args()

    if not args.api_key or not args.api_secret:
        print(
            "ERROR: LASTFM_API_KEY and LASTFM_API_SECRET must be set "
            "(env vars or --api-key/--api-secret).",
            file=sys.stderr,
        )
        return 2

    print("Requesting a Last.fm auth token...")
    token = _call("auth.getToken", args.api_key)["token"]

    auth_url = (
        f"https://www.last.fm/api/auth/?api_key={args.api_key}&token={token}"
    )
    print()
    print("Open this URL in a browser, log in, and click 'Yes, allow':")
    print()
    print(f"    {auth_url}")
    print()
    try:
        input("Press Enter once you've authorized... ")
    except EOFError:
        print("(stdin closed; assuming authorization complete)")

    print("Exchanging token for a permanent session key...")
    data = _call(
        "auth.getSession",
        args.api_key,
        secret=args.api_secret,
        extra={"token": token},
        signed=True,
    )
    session = data.get("session", {})
    sk = session.get("key")
    name = session.get("name")
    if not sk:
        print(f"ERROR: unexpected response: {data}", file=sys.stderr)
        return 1

    print()
    print(f"Success — authorized as: {name}")
    print()
    print("Paste this line into pi/.env:")
    print()
    print(f"    LASTFM_SESSION_KEY={sk}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
