---
name: nowplaying-setup-install
description: Use when the user is ready to install the Now Playing backend software on the Pi — cloning the repo, creating the Python venv, writing the .env, syncing their Discogs catalog, and manually verifying audio capture. Triggers on phrases like "install the software", "set up discogs sync", "run uv sync", "create the env file", "verify audio capture", "run capture_proto".
---

# Now Playing — software install

Get the orchestrator code installed and verified end-to-end before the systemd services are created. This phase ends with a working manual audio-capture run that prints heartbeats with a real `level_db` value.

**Prerequisite:** hardware is wired (`nowplaying-setup-hardware`), Pi is reachable (`nowplaying-setup-pi`), Sonos zone is configured (`nowplaying-setup-sonos`), Discogs token is in hand (`nowplaying-setup-accounts`).

## Step 1: Clone the repo

On the Pi (or `ssh nowplaying-pi '...'`):

```bash
ssh nowplaying-pi 'git clone <repo-url> ~/now-playing'
```

Replace `<repo-url>` with the canonical URL the user is installing from. If the user is mirroring from a fork, that URL works too.

## Step 2: Install uv and build the venv

```bash
ssh nowplaying-pi 'cd ~/now-playing/pi && curl -LsSf https://astral.sh/uv/install.sh | sh'
```

That installs `uv` to `~/.local/bin/uv`. Then:

```bash
ssh nowplaying-pi 'cd ~/now-playing/pi && ~/.local/bin/uv sync --extra audio --extra discogs --extra shazam'
```

This creates `pi/.venv/` and installs the three required extra groups:

- `audio` — sounddevice + numpy for capture
- `discogs` — discogs-client + sqlite helpers for catalog sync
- `shazam` — shazamio for the recognition cascade

**Verify the venv:**

```bash
ssh nowplaying-pi 'test -x ~/now-playing/pi/.venv/bin/python && echo OK'
```

## Step 3: Identify the USB audio device

The orchestrator auto-detects the UFO202 by name. Confirm it shows up:

```bash
ssh nowplaying-pi '~/now-playing/pi/.venv/bin/python -c "import sounddevice as sd; print(sd.query_devices())"'
```

Look for a line like:

```
3 USB Audio CODEC: - (hw:3,0), ALSA (2 in, 0 out)
```

The exact card number doesn't matter — the orchestrator finds the device by name match. What you need to confirm:

- The device is present with non-zero input channels.
- The name contains a recognizable substring (`CODEC`, `USB Audio`, etc.) that `pi/scripts/capture_proto.py:find_ufo202` will match.

If the device has a different name (some clones of the UFO202 do), note it — the user may need to edit `find_ufo202` later.

## Step 4: Write `pi/.env`

Create `~/now-playing/pi/.env` with the minimum required keys:

```
DISCOGS_TOKEN=<paste-from-accounts-phase>
SONOS_ZONE_NAME=<exact-zone-name>
```

Walk the user through it interactively. Easiest path:

```bash
ssh nowplaying-pi 'cp ~/now-playing/pi/.env.example ~/now-playing/pi/.env'
ssh nowplaying-pi 'nano ~/now-playing/pi/.env'   # or vi, or scp from Mac
```

Replace the placeholder values. Confirm permissions:

```bash
ssh nowplaying-pi 'chmod 600 ~/now-playing/pi/.env'
```

## Step 5: Sync the Discogs catalog

This is the longest single step in the install — 5–20 minutes for an average-sized collection, longer for thousands of releases. The script is idempotent (re-runs skip what's already synced), so an interrupted sync resumes cleanly.

```bash
ssh nowplaying-pi 'cd ~/now-playing/pi && .venv/bin/python scripts/discogs_sync.py'
```

The script runs three passes:

1. **Releases list** — your collection's release IDs and basic metadata. Fast.
2. **Per-release details** — full tracklist, label, year, formats. Slow (Discogs rate-limits aggressively).
3. **Cover-art download** — local cache of Discogs cover scans. Runtime album art is fetched live from MusicBrainz, but the cache is used as a fallback.

**Verify the catalog DB:**

```bash
ssh nowplaying-pi 'ls -la ~/now-playing/pi/data/discogs.sqlite'
```

Expect a file ≥ a few hundred KB. For a sanity check on row counts:

```bash
ssh nowplaying-pi 'sqlite3 ~/now-playing/pi/data/discogs.sqlite "SELECT count(*) FROM releases"'
```

## Step 6: Manual audio-capture verification

Before installing systemd services, run capture **interactively** so the user sees JSON heartbeats with their own audio levels. This is the single best way to confirm the whole physical chain is correct.

```bash
ssh nowplaying-pi 'cd ~/now-playing/pi && .venv/bin/python scripts/capture_proto.py'
```

Drop a needle on a record. Within ~15 seconds, expect JSON lines on stdout:

```
{"ts":"...","event":"started","device":"USB Audio CODEC: ...","silence_db":-45.0,"heartbeat_s":15.0}
{"ts":"...","event":"heartbeat","level_db":-22.4,"clip":"...","clip_seconds":10.0}
```

**Sanity check on `level_db`:** during music, it should average around **-25 to -15 dB**. Below -35 dB means the input gain is too low — check the preamp output and the UFO202's input level switch. Above -5 dB means clipping risk — back off the gain.

Ctrl+C to stop.

## You're done with this phase when

- `~/now-playing/pi/.venv/bin/python` exists and is executable.
- `~/now-playing/pi/.env` contains a real `DISCOGS_TOKEN` and the correct `SONOS_ZONE_NAME`.
- `~/now-playing/pi/data/discogs.sqlite` exists and has rows in `releases`.
- `capture_proto.py` printed `heartbeat` events with `level_db` between roughly -30 and -10 while music played.

## Next

Ask Claude to **"install the services"** → `nowplaying-setup-services`.

## When to switch skills

- `uv sync` fails on a dependency → `nowplaying-troubleshoot` (most common cause: missing system library, e.g. `portaudio19-dev`).
- `sounddevice.query_devices()` doesn't list the UFO202 → back to `nowplaying-setup-hardware` to check the USB cable / port.
- Discogs sync fails with auth error → back to `nowplaying-setup-accounts` to regenerate the token.
- `capture_proto.py` prints heartbeats but `level_db` is silent (<-50 dB) while music plays → check the splitter wiring; see `nowplaying-setup-hardware`.
