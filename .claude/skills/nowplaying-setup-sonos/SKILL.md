---
name: nowplaying-setup-sonos
description: Use when the user needs to configure their Sonos zone for Now Playing — picking a zone name, setting line-in as the playback source, confirming the Pi can reach Sonos over the network. Triggers on phrases like "configure sonos", "set up the zone", "sonos line in", "sonos zone name", "sonos vlan", "sonos discovery not working".
---

# Now Playing — Sonos configuration

Now Playing reads `SONOS_ZONE_NAME` from `pi/.env` and subscribes to that zone's UPnP "AV transport" events. This phase makes sure the zone is configured correctly and the Pi can see it. This is the phase where most non-obvious install failures actually originate — Sonos discovery is sensitive to network topology.

## Step 1: Decide on the zone name

Ask: **"What is the Sonos zone (room) you wired your turntable's line-in into?"** The name must match the Sonos app exactly — case-sensitive, including spaces. Common examples: `Office`, `Living Room`, `Den`.

Avoid emoji or unicode in the zone name even if Sonos allows them — pyparsing edge cases in the SoCo library have been known to misbehave. Plain ASCII is safest.

## Step 2: Set line-in as the playback source

In the Sonos app on your phone:

1. Open the zone (room) you wired.
2. Tap the source/now-playing area → **Line-In** → pick the device labeled with this room.
3. Confirm music plays from the room speakers when a record is on the turntable.

This step is non-obvious because the Sonos app doesn't make "line-in" prominent — it's usually buried under a sources list. If you've never used line-in on this zone before, the app may also prompt you to enable it once.

## Step 3: Verify the Pi can reach the Sonos zone

From the Pi:

```bash
ssh nowplaying-pi 'ping -c1 <sonos-zone-ip>'
```

To find the zone IP: in the Sonos app → Settings → System → pick the room → IP Address.

If the ping succeeds, your Pi and Sonos are on the same routable network. If it fails, you have a network problem — see "Common network gotchas" below.

## Step 4: Verify UPnP discovery actually works

The orchestrator uses SoCo to discover zones via SSDP multicast. Ping is not a sufficient test — UPnP multicast can be filtered by switches even when unicast routing works.

If the repo is already cloned and the venv is built (later phase), the quick discovery test is:

```bash
ssh nowplaying-pi 'cd ~/now-playing/pi && .venv/bin/python -c "import soco; print([z.player_name for z in soco.discover() or []])"'
```

You don't have to do this now — it'll be re-verified once services are running. But knowing the failure mode helps:

- **Empty list `[]`** → multicast not reaching the Pi. VLAN or managed-switch issue.
- **List that doesn't include your zone name** → zone is on a different LAN segment, or the Sonos system is split across two coordinator groups (rare).

## Common network gotchas

| Symptom | Most likely cause | Fix |
|---------|------------------|-----|
| `ping` fails | Pi and Sonos on different VLANs | Move Pi to the Sonos VLAN, or set up proper inter-VLAN routing. |
| `ping` succeeds, discovery returns `[]` | Multicast filtering on a managed switch (IGMP snooping with no querier, or SSDP filtering enabled) | Enable an IGMP querier on the switch, or disable SSDP filtering. |
| `ping` succeeds, discovery finds zones but not yours | Your Sonos system has a secondary "controller-only" device that isn't running line-in | Double-check zone name in the Sonos app; verify the room you chose actually has a line-in jack. |
| Discovery works once, then stops | UPnP subscription dropped without renewal | This is a known orchestrator-level concern; the auto-renew loop handles it. If discovery works during install, it'll keep working. |

## You're done with this phase when

- You know the exact case-sensitive zone name and can write it down.
- The Sonos app shows line-in as the active source for that zone.
- Audible music plays through that zone when a record spins.
- `ping <sonos-zone-ip>` from the Pi succeeds.

## Next

Ask Claude to **"set up my discogs account"** → `nowplaying-setup-accounts`.

## When to switch skills

- Pi can't reach the Sonos network at all → fix routing first; this is upstream of the project.
- Sonos zone discovery looked fine here but fails after services start → `nowplaying-troubleshoot` (the orchestrator has more diagnostics once it's running).
