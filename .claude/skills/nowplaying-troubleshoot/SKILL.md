---
name: nowplaying-troubleshoot
description: Use when the user reports the Now Playing kiosk isn't working — service failing, audio device not found, no recognition firing, kiosk showing wrong info, or any error during install. Triggers on phrases like "it's not working", "the kiosk is blank", "no audio", "service won't start", "debug my install".
---

# Now Playing — troubleshooting

Diagnose Now Playing problems systematically. Walk through the most-likely causes first.

## Step 1: Identify the failure mode

Ask the user what they're seeing. Map the answer to one of:

- **No display at all / blank screen** — kiosk service issue or orchestrator down.
- **Kiosk shows the clock when a record is playing** — capture or recognition issue.
- **Kiosk shows wrong track / wrong artist** — recognition false-positive or stale state.
- **systemd service stuck restarting** — orchestrator import error or crash.
- **Discogs sync failed** — auth / network / Discogs API issue.

## Step 2: Gather diagnostics

For any failure, get these in parallel (over SSH to the Pi):

```bash
systemctl is-active nowplaying-orchestrator nowplaying-kiosk
systemctl status nowplaying-orchestrator --no-pager
journalctl -u nowplaying-orchestrator -n 50 --no-pager
journalctl -u nowplaying-kiosk -n 50 --no-pager
curl -fsS http://localhost:8080/health
ls /usr/bin/chromium
~/now-playing/pi/.venv/bin/python -c "import sounddevice as sd; print(sd.query_devices())" 2>&1 | head -10
```

## Step 3: Common failure modes and fixes

### "ModuleNotFoundError: No module named '<dep>'"
The orchestrator is using system Python instead of the venv. Check the systemd unit's `ExecStart` points at `<home>/now-playing/pi/.venv/bin/python`, then re-run `uv sync` inside `pi/`.

### "UFO202 (CODEC) not found" / capture_proto.py exit 1
USB device not recognized or not under that name. Re-check with:
```bash
~/now-playing/pi/.venv/bin/python -c "import sounddevice as sd; [print(i, d) for i, d in enumerate(sd.query_devices())]"
```
If the user's device is different, they can pass `--device <idx>` to capture_proto.py, or edit `pi/scripts/capture_proto.py:find_ufo202` to match their device name string.

### Sonos zone discovery times out
The orchestrator reads `SONOS_ZONE_NAME` from `pi/.env`. Confirm:
1. Zone name matches the Sonos app exactly (case-sensitive).
2. Pi is on the same VLAN as the Sonos system (UPnP multicast doesn't cross VLAN boundaries by default).
3. Pi can reach the Sonos zone: `ping <sonos-ip>`.

### Kiosk service fails with "no graphical session"
Expected if the Pi is in console-mode. Either boot to graphical (`sudo raspi-config` → System Options → Boot/Auto Login → Desktop Autologin), or accept that the kiosk only runs when the user is at the display.

### Kiosk shows previous track forever / never updates
Probably the orchestrator's `last_vinyl` is stuck. Restart the service:
```bash
sudo systemctl restart nowplaying-orchestrator
```
Non-destructive — clears in-memory state, next recognition repopulates.

### Recognition picks wrong song occasionally
Shazam returns a confident-but-wrong guess on thin signal. The orchestrator has a quality gate (`release_id is None AND confidence < 0.7` is dropped) — if it's still happening, the audio chain may need tuning.

Use a bounded query (full journal scan is slow on the Pi):
```bash
journalctl -u nowplaying-orchestrator --since "1 hour ago" --no-pager | grep "capture heartbeat" | tail -10
```

### Discogs sync hangs or fails
Network issue, bad token, or rate-limited. Verify auth:
```bash
curl -fsS "https://api.discogs.com/users/$DISCOGS_USERNAME" \
  -H "Authorization: Discogs token=$DISCOGS_TOKEN" | head -20
```

## Step 4: If none of the above match

Pull a wider log slice:
```bash
journalctl -u nowplaying-orchestrator --since "1 hour ago" --no-pager | tail -200
```
Look for tracebacks. Walk the user through the first one.
