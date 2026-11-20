# Example log output

What the orchestrator looks like running. Each slice is a real journalctl excerpt with timestamps preserved; LAN IPs and the Sonos zone UUID have been scrubbed. Tail any of these patterns yourself with:

```bash
journalctl -u nowplaying-orchestrator -f
```

The `nowplaying-diagnose` skill (`.claude/skills/nowplaying-diagnose/SKILL.md`) has more interpretation patterns and filter commands.

## A track changes

Shazam identifies the new track on the first heartbeat after the audio transitions. The broadcaster pushes the new payload to every connected WebSocket client.

```
nowplaying.main           recognize: method=shazam release_id=31427573 artist=Failure title=Sergeant Politeness
nowplaying.api            publish: clients=3 source=vinyl title='Sergeant Politeness' release_id=31427573
```

## Steady state inside a track

Every 15s heartbeat re-confirms the same track. The broadcaster sees the payload is content-identical to the last one and suppresses the publish, so the kiosk never sees a duplicate update.

```
nowplaying.vinyl          capture heartbeat: clip=pi/data/clips/1779284420_heartbeat.wav level_db=-21.3
nowplaying.vinyl.ratelimit shazam: call 25 (4 in last 60s)
nowplaying.main           recognize: method=shazam release_id=31427573 artist=Failure title=Sergeant Politeness
nowplaying.api            publish: redundant (skipped) reason=content-identical source=vinyl title='Sergeant Politeness'

nowplaying.vinyl          capture heartbeat: clip=pi/data/clips/1779284435_heartbeat.wav level_db=-20.8
nowplaying.main           recognize: method=shazam release_id=31427573 artist=Failure title=Sergeant Politeness
nowplaying.api            publish: redundant (skipped) reason=content-identical source=vinyl title='Sergeant Politeness'
```

The `level_db` field is the rolling RMS of the input — useful for sanity-checking the audio chain. During music it sits around -20 dB; below -30 dB (the music threshold; -34 dB is the silence floor) usually means the pre-amp output is too low or the line-in cable is loose.

## Sonos UPnP subscription renewal

Every 10 minutes the orchestrator's watchdog notices no events have arrived from the Sonos zone and re-subscribes. The renewal triggers a duplicate UPnP NOTIFY which would otherwise flash the kiosk back to "vinyl identifying" for a heartbeat — the broadcaster's content-equality check filters it.

```
nowplaying.sonos.listener [sonos] watchdog: no events for 600s (threshold=600s) — running liveness probe
nowplaying.sonos.listener [sonos] resubscribing (no events for 600s (threshold=600s))...
nowplaying.sonos.listener [sonos] subscribed (sid=uuid:RINCON_<sonos-uuid>_sub0000001610 timeout=86400s)
nowplaying.sonos.listener [sonos] resubscribed successfully
```

## Tracklist-aware advancement (Shazam catalog gap)

Some tracks aren't in Shazam's catalog — short interludes, segues, deep cuts on B-sides. When Shazam misses twice in a row on an album that's already locked, the orchestrator advances to the next track in the locked album's tracklist and publishes a *predicted* payload so the kiosk keeps showing useful info instead of dropping to "couldn't identify."

```
nowplaying.main           recognize: method=unmatched release_id=None artist=None title=None
nowplaying.main           fingerprint: no match for release=31427573
nowplaying.main           music-level unmatched: streak=1/2 (waiting)

nowplaying.main           recognize: method=unmatched release_id=None artist=None title=None
nowplaying.main           fingerprint: no match for release=31427573
nowplaying.main           predicted: advanced to side=A position=A3 title='Segue 1'
nowplaying.api            publish: clients=3 source=vinyl title='Segue 1' release_id=31427573
```

The published payload carries `match_method: "predicted"` and `predicted: true` so the kiosk can render it differently (italic title, dimmed opacity, "BEST GUESS · tap to confirm" badge).

## State decay → NEEDS_ID

This applies only when no side timeline is followed (a release with no track list). While a side is followed, a track whose listed time is up gives way to the next track on the side, and the last track of a side stays until a flip, a match or idle.

If predicted advancement runs out of confidence — say, the predicted track also can't be Shazam-confirmed and the track's expected play time is up (or, when the track's duration is unknown, the last real recognition is more than 45 seconds old) — the orchestrator falls through to NEEDS_ID and the kiosk shows the "couldn't identify" screen with a manual-pick link.

```
nowplaying.main           recognize: method=unmatched release_id=None artist=None title=None
nowplaying.main           fingerprint: no match for release=31427573
nowplaying.main           state-decay: last_vinyl='Sergeant Politeness' past expected track end (stamp age=275.6s) — forcing needs-id
nowplaying.main           NEEDS_ID: level_db=-23.3 prev=Sergeant Politeness
nowplaying.api            publish: clients=3 source=vinyl title=None release_id=None
```

## Reading these in the field

```bash
# Live tail, all events:
journalctl -u nowplaying-orchestrator -f

# Just recognition + publish:
journalctl -u nowplaying-orchestrator -f | grep -E "(recognize|publish|predicted|NEEDS_ID|state-decay)"

# Just capture levels (audio chain sanity):
journalctl -u nowplaying-orchestrator -f | grep "capture heartbeat"

# Just Sonos UPnP activity:
journalctl -u nowplaying-orchestrator -f | grep sonos.listener
```

See [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) for the module and symbol behind each emitter.
