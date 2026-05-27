---
name: nowplaying-setup-lastfm
description: Use when the user wants to enable Last.fm scrobbling on their Now Playing kiosk — guides them through creating a Last.fm API app, minting a permanent session key via the browser-auth dance, writing the three env vars into the Pi's .env, and restarting the orchestrator. Triggers on phrases like "set up last.fm", "enable scrobbling", "scrobble to last.fm", "configure lastfm", "lastfm auth".
---

# Now Playing — Last.fm scrobbling setup

Get confirmed plays scrobbled to the user's Last.fm profile. Three Last.fm-issued values land in `pi/.env`; the orchestrator picks them up on restart.

Last.fm scrobble policy enforced by the orchestrator (see `pi/nowplaying/history/scrobble.py:75`):
- Track must be **≥ 30 seconds long**
- Listener must have heard **≥ 50% of it OR ≥ 240 seconds**

Same rule the official Last.fm clients use. Shorter tracks (sub-30s segues, interludes) are intentionally not scrobbled.

## Prerequisites

- The user has a Last.fm account (free — sign up at <https://www.last.fm>).
- The orchestrator is installed and running (`nowplaying-setup-services` complete).
- Access to a machine with a browser for the authorization step (Mac or Pi both work; the Mac is usually easier).

## Step 1: Create a Last.fm API application (one-time)

The user creates this once per Last.fm account; the same app can be reused if they redeploy or move installs.

1. Open <https://www.last.fm/api/account/create>.
2. Fill in:
   - **Application name**: anything (e.g. "now-playing-pi")
   - **Application description**: optional — "Personal scrobbling kiosk"
   - **Application homepage URL**: a fork URL or `http://localhost` — Last.fm doesn't validate this
   - **Callback URL**: leave blank (desktop app pattern)
3. Submit. Last.fm shows:
   - **API Key** — public-ish, e.g. `5fbff1ab0d3761be7e0a6fb3d1394899`
   - **Shared Secret** — keep private, e.g. `4f268997823b8884605197feb0023f28`

Have the user copy both values somewhere safe (password manager). They'll go into `pi/.env` in step 3.

## Step 2: Mint a permanent session key (one-time)

This is the OAuth-style dance — Last.fm needs the user to explicitly authorize the app to scrobble on their behalf. The session key never expires unless the user revokes it at <https://www.last.fm/settings/applications>.

The repo ships a helper script: `pi/scripts/lastfm_auth.py`.

**Run it locally on the Mac** (easier — browser is right there):

```bash
cd ~/GitHub/schuettc/now-playing/pi
LASTFM_API_KEY=<api-key-from-step-1> \
LASTFM_API_SECRET=<shared-secret-from-step-1> \
.venv/bin/python scripts/lastfm_auth.py
```

OR **on the Pi** (also works; `uv` invocation):

```bash
ssh nowplaying-pi 'cd ~/now-playing/pi && \
  LASTFM_API_KEY=<api-key> LASTFM_API_SECRET=<secret> \
  uv run python scripts/lastfm_auth.py'
```

The script:
1. Requests a Last.fm auth token (one round-trip)
2. Prints a URL: `https://www.last.fm/api/auth/?api_key=…&token=…`
3. **User opens that URL in a browser, logs into Last.fm, clicks "Yes, allow"**
4. Returns to terminal, presses Enter
5. Exchanges the (now-authorized) token for a permanent session key
6. Prints `Success — authorized as: <username>` and `LASTFM_SESSION_KEY=…`

Have the user copy the entire `LASTFM_SESSION_KEY=…` line.

### If the script can't get a session key

- **`Last.fm error 14: Unauthorized Token`** — the user didn't click "Yes, allow" before pressing Enter. Re-run the script; click "Yes, allow" first this time.
- **`Last.fm error 4: Invalid authentication`** — the API key or shared secret is wrong (typo on copy-paste). Verify against the Last.fm API account page.
- **`urllib.error.URLError`** — network problem. Check from a different terminal that the user can `curl https://ws.audioscrobbler.com/2.0/`.

## Step 3: Write all three keys into `pi/.env` on the Pi

```bash
ssh nowplaying-pi "cat >> ~/now-playing/pi/.env <<EOF

# Last.fm scrobbling
LASTFM_API_KEY=<api-key>
LASTFM_API_SECRET=<shared-secret>
LASTFM_SESSION_KEY=<session-key>
EOF
chmod 600 ~/now-playing/pi/.env"
```

Confirm `pi/.env` doesn't have placeholder values from earlier `cp .env.example .env` runs — search-and-replace if needed:

```bash
ssh nowplaying-pi 'grep "^LASTFM_" ~/now-playing/pi/.env'
```

All three lines should show real values, not `#` commented-out templates.

## Step 4: Restart the orchestrator

```bash
ssh nowplaying-pi 'sudo systemctl restart nowplaying-orchestrator'
```

The orchestrator picks up env vars only at startup, so the restart is mandatory.

## Step 5: Verify

The **boot log doesn't mention scrobbling** — `_log_state_once` in `pi/nowplaying/scrobble.py:46` runs lazily on the first scrobble call (or first miss-due-to-disabled call). So `journalctl … | grep scrobble` will be empty until something tries to scrobble.

To trigger a real scrobble: play a track that's at least 30s long, and either play ≥50% of it or wait ≥240s. Then:

```bash
ssh nowplaying-pi 'journalctl -u nowplaying-orchestrator --since "10 min ago" --no-pager | grep -i scrobble'
```

Expected:

- `Last.fm scrobbling enabled` — fires on the first scrobble attempt (proves the env vars are picked up).
- `update_now_playing: <artist> — <title>` — fires when a track first locks (the "Now Playing" indicator on the user's Last.fm profile).
- `Scrobbled to Last.fm: <artist> — <title>` — fires when the play-eligibility threshold trips. Per-track once.

If `update_now_playing` fires but no `Scrobbled` follows after a few minutes of listening, the track was shorter than 30s or you skipped it before the 50%/240s threshold. Expected.

## What this skill is NOT

- Recovery for a leaked session key — Last.fm doesn't expose key rotation in the API. If a session key leaks, revoke at <https://www.last.fm/settings/applications> and re-run step 2 with the same API key + secret.
- Last.fm read-back ("show me what I've scrobbled") — out of scope. The Last.fm site / mobile app does that natively.
- A way to scrobble retroactively — only confirmed plays from after the env vars are set get scrobbled.

## When to switch skills

- The user wants to verify a scrobble fired but the journal is silent → `nowplaying-diagnose` for a broader log scan.
- The orchestrator won't restart → `nowplaying-troubleshoot`.
- The user wants to disable scrobbling → tell them to delete the three `LASTFM_*` lines from `pi/.env` and restart the orchestrator. The scrobble module silently no-ops without them.

## Done with this phase when

- All three `LASTFM_*` lines are in `pi/.env` with real values.
- `pi/.env` has `chmod 600` permissions.
- Orchestrator is `active` after restart.
- (Eventually) a real play has triggered a `Scrobbled to Last.fm: …` log line.
