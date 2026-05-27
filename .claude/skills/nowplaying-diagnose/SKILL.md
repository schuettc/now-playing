---
name: nowplaying-diagnose
description: Use when the user wants to inspect Now Playing runtime state without changing anything — peek at processes, recent recognitions, capture levels, hygiene activity, promotion progress. Triggers on "what's it doing", "show me logs", "is recognition working", "inspect the orchestrator".
---

# Now Playing — runtime inspection

Read-only inspection of the live system. Run commands over SSH, present what you find, don't modify state.

## Quick health check

```bash
systemctl is-active nowplaying-orchestrator nowplaying-kiosk
curl -fsS http://localhost:8080/health
ps -ef | grep -E "nowplaying.main|capture_proto" | grep -v grep
```

## Recent recognition activity

```bash
journalctl -u nowplaying-orchestrator -n 200 --no-pager | grep -E "recognize:|fingerprint:|publish:|predicted:|promotion:|hygiene|discovery:|track-guess:"
```

Interpret:
- `recognize: method=shazam release_id=... artist=... title=...` — Shazam returned a match. Primary recognition path. `release_id=None` with `release_mbid=<uuid>` on the publish payload = no-Discogs path; the record was auto-discovered via MusicBrainz.
- `recognize: method=unmatched ...` — Shazam returned no confident match. Triggers the fingerprint cascade (if `FINGERPRINT_ENABLED=true`) and/or the unmatched-streak path.
- `discovery: discovered release mbid=<uuid> artist=... album=...` — background MusicBrainz lookup persisted a release + tracklist into `discovered.sqlite` for an off-Discogs record. Fires once per (artist, album).
- `discovery: no MB match for artist=... album=...` — MusicBrainz miss; negative-cached in `discovered.sqlite.negative_lookups` to avoid re-querying.
- `fingerprint: matched release=<id> pos=<pos> hits=<n>` — local fingerprint cascade confirmed a track within a Discogs-locked album.
- `fingerprint: matched ... mbid=<uuid> pos=<pos> hits=<n>` — MBID-keyed cascade hit against `discovered.sqlite` (no-Discogs path; the locked release was auto-discovered via MusicBrainz, not from the local Discogs catalog).
- `fingerprint: blind matched release=<id> pos=<pos> hits=<n>` — blind-fingerprint discovery hit (no album lock yet).
- `fingerprint: blind scan — no lock, scanning all refs` — blind union scan across both fingerprint.db and discovered.sqlite is running.
- `fingerprint: no match for release=<int>` / `fingerprint: no match for mbid=<uuid>` / `fingerprint: blind scan — no match across all refs` — fingerprint cascade ran but produced nothing actionable.
- `fingerprint: below threshold (hits=...)` / `fingerprint: insufficient margin (top=... runner_up=...)` — cascade saw a candidate but rejected it on the quality gate. Working as designed.
- `fingerprint: anchor set release=... pos=... hits=... duration=...` — a confirmed fingerprint hit installed an anchor that the predicted-advance path can ride.
- `predicted: advanced to side=<s> position=<pos> title=...` — predicted-advance fired on top of an anchor or pin. Kiosk now shows a heuristic guess.
- `promotion: coverage-gap (pin|anchor) release=... pos=... track_position_s=... duration=... — scheduling` — a Discogs-keyed coverage promotion has been queued into fingerprint.db.
- `promotion: coverage-gap (anchor) mbid=<uuid> ...` — MBID-keyed promotion into discovered.sqlite (no-Discogs path; fires after sustained playback).
- `track-guess: LLM picked position=<pos> confidence=<...> alt={...} — <reason>` / `track-guess: heuristic pos=<pos>` — BEST GUESS card decision. Works on both Discogs and discovered releases now that `track_position` resolves on the discovered path.
- `publish: clients=<n> source=<vinyl|airplay> title=... release_id=...` — broadcaster sent the payload to the kiosk(s).
- `publish: redundant (skipped) reason=<r> source=<s> title=...` — de-dup suppressed a re-publish of the same state.
- `hygiene sweep: {...}` — periodic disk cleanup ran; reports kept / deleted_candidate / deleted_orphan counts.

Confirm the fingerprint cascade is wired up at boot:
```bash
journalctl -u nowplaying-orchestrator --no-pager | grep -E "features:" | tail -1
```
Expect `features: fingerprint=on llm=on|off`. If `fingerprint=off`, the cascade is env-gated off — set `FINGERPRINT_ENABLED=true` in `pi/.env`.

## Capture levels (audio chain sanity)

```bash
journalctl -u nowplaying-orchestrator -n 300 --no-pager | grep -E "capture (heartbeat|instant|silent|audible):" | tail -20
```
Look at `level_db=` on the `capture heartbeat:` lines. During music it should average around -25 to -15 dB. Below -35 dB suggests the signal is too quiet (check pre-amp / line-in routing). `capture silent:` / `capture audible:` lines mark the gate transitions between music and silence.

## LLM-assist activity

```bash
# Was LLM enabled at last boot?
journalctl -u nowplaying-orchestrator --no-pager | grep -E "features:" | tail -1

# Recent LLM verdicts (each marker corresponds to one of the four hooks)
journalctl -u nowplaying-orchestrator -n 500 --no-pager | grep -E "shazam-relevance:|advance-track:|track-change-llm:|track-guess:|llm: .*call failed"
```

Interpret:
- `features: ... llm=on` — `ANTHROPIC_API_KEY` is set in `pi/.env` and LLMAssist will fire on the configured triggers. `llm=off` means every hook short-circuits to the heuristic path.
- `shazam-relevance: rejected — <reason>` — `judge_shazam_result` blocked a cross-album Shazam hit. No log line when accepted.
- `advance-track: LLM advances to index=N position=X` / `LLM says stay on <title>` — `judge_advance` decided an end-of-side advance target.
- `track-change-llm: decision=advance|hold confidence=X.XX advance_to=Y` — `decide_track_change` override on Rule A's mid-track suppression.
- `track-guess: LLM picked position=X confidence=...` / `track-guess: heuristic pos=X` — `judge_track_guess` for the kiosk BEST GUESS card (LLM vs heuristic fallback).
- `llm: <method> call failed: <repr>` — API timeout, rate limit, or parse failure. Throttled to once per exception type per 300s.

For a live color-coded tail during a listening session: `pi/scripts/monitor_llm.sh`.

## Storage status

```bash
ls ~/now-playing/pi/data/clips/ 2>/dev/null | wc -l
du -sh ~/now-playing/pi/data/clips/ ~/now-playing/pi/data/art/ 2>/dev/null
ls -lh ~/now-playing/pi/data/fingerprint.db ~/now-playing/pi/data/discogs.sqlite ~/now-playing/pi/data/discovered.sqlite ~/now-playing/pi/data/play_history.sqlite 2>/dev/null
```

Interpret:
- `pi/data/fingerprint.db` — SQLite store for the Discogs-release-keyed fingerprint cascade (refs + hashes). Missing/empty means the cascade has nothing to match against; populated via pin-driven promotion.
- `pi/data/discogs.sqlite` — local Discogs catalog snapshot. Used for Shazam reverse-lookup and fingerprint payload enrichment when present; the orchestrator tolerates it being missing and falls back to the MusicBrainz discovery path.
- `pi/data/discovered.sqlite` — MBID-keyed catalog for releases not in Discogs. Populated automatically: a background MusicBrainz lookup runs on each off-Discogs Shazam hit and persists the release + tracklist; the local fingerprint cascade then promotes MBID-keyed refs into the same DB as the user plays the record. Holds `releases`, `tracks`, `negative_lookups`, `fp_refs`, `fp_hashes`.
- `pi/data/play_history.sqlite` — recorded plays.
- `pi/data/clips/` — raw heartbeat clips awaiting hygiene sweep.
- `pi/data/art/` — cached cover art (Discogs + MusicBrainz + overrides).

## Live WebSocket stream (one-shot)

```bash
# Read 10 seconds of WS traffic to see real-time publishes:
timeout 10 ~/now-playing/pi/.venv/bin/python -c "
import asyncio, aiohttp
async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect('http://localhost:8080/ws') as ws:
            async for msg in ws:
                print(msg.data)
asyncio.run(main())
" 2>/dev/null
```

## When inspection reveals a problem

Switch to `nowplaying-troubleshoot`.
