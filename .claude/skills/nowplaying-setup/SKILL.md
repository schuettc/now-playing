---
name: nowplaying-setup
description: Use when the user wants to install or set up the Now Playing kiosk on a Raspberry Pi for the first time. Triggers on phrases like "help me set this up", "install nowplaying on my pi", "get started", "first time setup", or when the user has just cloned the repo and asks how to begin.
---

# Now Playing — install coordinator

You are guiding a new installer through bringing up the Now Playing kiosk. The install is broken into focused phase skills. Your job is to figure out **where the user already is**, hand off to the right phase, and stop. Do not try to do all phases at once.

## Phase map

| Phase | When it applies | Skill to hand off to |
|-------|-----------------|----------------------|
| 1. Hardware wiring | Turntable, preamp, splitter, Sonos line-in, UFO202, display | `nowplaying-setup-hardware` |
| 2. Pi preparation | Flash Pi OS, hostname, SSH access, system packages | `nowplaying-setup-pi` |
| 3. Sonos configuration | Zone name, line-in source, VLAN/discovery | `nowplaying-setup-sonos` |
| 4. Discogs account | Recommended; skippable if you want to try MusicBrainz-only first | `nowplaying-setup-accounts` |
| 5. Software install | Clone, `uv sync`, `.env`, catalog sync, capture verify | `nowplaying-setup-install` |
| 6. Services + kiosk | systemd units, kiosk URL, optional fingerprint flag | `nowplaying-setup-services` |
| 7. Last.fm scrobbling (optional) | API app + browser auth dance + three env vars | `nowplaying-setup-lastfm` |

Phases 1–6 are sequential; each assumes the previous is complete. Phase 7 is opt-in and can be done any time after the services are running.

## Step 1: Figure out where the user is

Ask in this order, one at a time, only the ones you can't infer from context (memory, recent messages, or a quick state probe):

1. **"Are you driving this from your Mac (or another machine via SSH), or working directly on the Pi?"** Both are supported. The commands are the same; the difference is whether they run locally or wrapped in `ssh nowplaying-pi '...'`. Remember the answer.

2. **"Is the audio chain wired up yet — turntable feeding both your Sonos line-in and the USB capture device on the Pi?"** If no → hand off to `nowplaying-setup-hardware`.

3. **"Is Pi OS flashed, hostname set, and can you SSH into it?"** If no → hand off to `nowplaying-setup-pi`.

4. **"Is your Sonos zone configured for line-in playback and named the way you'll reference it in config?"** If no → hand off to `nowplaying-setup-sonos`.

5. **"Do you have a Discogs personal access token ready?"** If no → hand off to `nowplaying-setup-accounts`. Follow-up: **"If you'd rather skip Discogs and rely on MusicBrainz auto-discovery, that's supported — say so and we'll skip Phase 4."** If they want to skip, note that decision and move to step 6.

6. **Probe: does the Pi have the repo cloned and the catalog synced?** Run:
   ```bash
   ssh nowplaying-pi 'test -f ~/now-playing/pi/data/discogs.sqlite && echo OK'
   ```
   If OK → the Discogs catalog is in place; continue to step 7. If not OK → ask **"Are you skipping Discogs and using MusicBrainz-only?"** If yes, also check for an installed venv (`ssh nowplaying-pi 'test -d ~/now-playing/pi/.venv && echo OK'`); if that's missing → hand off to `nowplaying-setup-install`. If they're not skipping → hand off to `nowplaying-setup-install` to run the catalog sync.

7. **Probe: are the services running?** Run:
   ```bash
   ssh nowplaying-pi 'systemctl is-active nowplaying-orchestrator nowplaying-kiosk'
   ```
   If both `active` → install is done; point them at the kiosk URL (`http://nowplaying-pi.local:8080/`). Otherwise → hand off to `nowplaying-setup-services`.

## Step 2: Hand off, then stop

Once you've identified the right phase, **say which phase you're starting and why**, then invoke that phase skill. Don't try to also do the next phase in the same turn — let the phase skill run, verify completion, and return control here for the next handoff.

If the user is mid-install and a phase fails, switch to `nowplaying-troubleshoot`.

## What this skill is NOT

- A full runbook. The phase skills hold the actual steps.
- A diagnostic tool. For "what's it doing right now" use `nowplaying-diagnose`. For "what's playing" use `nowplaying-status`. For "it's broken" use `nowplaying-troubleshoot`.

If the user says "just walk me through the whole thing," run the phases in order — but still one at a time, verifying each before moving to the next.
