---
name: nowplaying-setup-services
description: Use when the user is ready to install the systemd services for Now Playing — the orchestrator and the kiosk Chromium launcher — and confirm both are running and the kiosk URL is reachable. Triggers on phrases like "install the services", "set up systemd", "start the kiosk", "enable autostart", "verify services running", "open the kiosk".
---

# Now Playing — services and kiosk launch

Install both systemd units, verify they come up clean, and open the kiosk. This is the last required phase of install. By the time you're done, the kiosk should be live on `http://nowplaying-pi.local:8080/`.

**Prerequisite:** `nowplaying-setup-install` is done — venv exists, `.env` is populated, catalog is synced, manual capture verification was green.

## Step 1: Run the installer

The installer is `pi/systemd/install.sh`. It substitutes the running user and home directory into the unit templates and installs them under `/etc/systemd/system/`.

```bash
ssh nowplaying-pi 'cd ~/now-playing && sudo bash pi/systemd/install.sh'
```

The script detects the installer user via `$SUDO_USER`. If you need to install for a different user (the orchestrator should run as `pi` but the installer ran as someone else), pass it explicitly:

```bash
ssh nowplaying-pi 'cd ~/now-playing && sudo NOWPLAYING_USER=pi bash pi/systemd/install.sh'
```

The script installs **both** units:

- `nowplaying-orchestrator.service` — the FastAPI + audio capture + recognition + Sonos listener.
- `nowplaying-kiosk.service` — the Chromium kiosk launcher (only meaningful on Pis with a display + graphical session).

## Step 2: Verify both services are active

```bash
ssh nowplaying-pi 'systemctl is-active nowplaying-orchestrator nowplaying-kiosk'
```

Expect two lines of `active`. Then look at the orchestrator's last log lines:

```bash
ssh nowplaying-pi 'journalctl -u nowplaying-orchestrator -n 30 --no-pager'
```

Expected highlights:

```
features: fingerprint=off llm=off
capture started: device=USB Audio CODEC... silence_db=-15.0 heartbeat_s=15.0
sonos: connected to zone=<your zone name>
```

If the capture line is missing, the USB device wasn't ready in time — see the boot-timing note below. If the sonos line is missing, the zone name in `.env` doesn't match what the SoCo discovery returned.

## Step 3: Confirm the HTTP server is reachable

From the Pi:

```bash
ssh nowplaying-pi 'curl -fsS http://localhost:8080/health'
```

Expect `ok`. Then from the Mac (or any device on the LAN):

```bash
curl -fsS http://nowplaying-pi.local:8080/health
```

Same response. If LAN access fails but localhost works, you have a firewall on the Pi (rare on Pi OS defaults — check `sudo nft list ruleset`).

## Step 4: Open the kiosk

From any device on the LAN:

```
http://nowplaying-pi.local:8080/
```

If the kiosk display is attached to the Pi and a graphical session is running, the kiosk service has already auto-launched Chromium against this URL. The kiosk unit waits for `/health` to return before opening the browser, so it doesn't race the orchestrator.

**What you should see:**

- **No record playing:** a clock face. This is the idle state.
- **Record playing for ~15 seconds:** the kiosk swaps to a now-playing card. First-recognition latency is roughly one capture heartbeat (~15s) plus a Shazam roundtrip (~1–2s).
- **Record stops:** clock returns after ~50 seconds (5s of silence detection + 45s idle timer).

## Step 5 (optional): Enable local fingerprint fallback

The local fingerprint DB is a free upgrade after a few playthroughs of an album — once it learns a track, Shazam misses on that track stop showing as gaps on the kiosk.

It's off by default. To turn it on, add to `pi/.env`:

```
FINGERPRINT_ENABLED=true
```

Then re-install the optional extra and restart:

```bash
ssh nowplaying-pi 'cd ~/now-playing/pi && ~/.local/bin/uv sync --extra audio --extra discogs --extra shazam --extra fingerprint && sudo systemctl restart nowplaying-orchestrator'
```

Confirm the cascade picked it up:

```bash
ssh nowplaying-pi 'journalctl -u nowplaying-orchestrator | grep features:'
```

Expect `features: fingerprint=on llm=off` (or `llm=on` if you've also set `ANTHROPIC_API_KEY`). The reference DB starts empty and populates automatically as confirmed Shazam hits roll in. After a couple of full album plays, expect to see lines like `fingerprint: matched ...` in the journal — that's the cascade saving a Shazam call.

This is the only optional flag worth recommending at install time. Everything else is operational tuning.

## Boot timing and USB audio (informational)

On a cold reboot the USB CODEC takes a few seconds longer to enumerate than the orchestrator takes to start. `capture_proto.py` retries device-open with backoff for ~30 seconds, so recognition is reliably online within ~30s of boot. If the capture line takes that long to appear in the journal on the first boot, that's normal — don't escalate.

## You're done with this phase when

- `systemctl is-active nowplaying-orchestrator nowplaying-kiosk` returns two `active`s.
- `curl http://nowplaying-pi.local:8080/health` returns `ok` from another machine on the LAN.
- A record playing on the turntable causes the kiosk URL to display a now-playing card within ~20 seconds.
- (If you took the optional fingerprint step) `journalctl | grep features:` shows `fingerprint=on`.

## You're done with the install

Install is complete. From here:

- **"What's playing right now"** → `nowplaying-status`
- **"Show me what the orchestrator is doing"** → `nowplaying-diagnose`
- **Something broken** → `nowplaying-troubleshoot`

## When to switch skills

- Either service stuck in `activating` / `failed` → `nowplaying-troubleshoot`.
- Kiosk reachable on localhost but not from LAN → check Pi firewall (rare), then `nowplaying-troubleshoot`.
- Kiosk loads but shows the clock indefinitely while a record plays → the audio chain stopped working between `capture_proto.py` verification and service start. Most often this is a swapped USB cable. Run `capture_proto.py` manually again to isolate.
