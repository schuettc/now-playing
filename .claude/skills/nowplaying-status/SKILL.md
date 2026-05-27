---
name: nowplaying-status
description: Use when the user asks "what's playing right now" or wants the current track info without opening the kiosk. Just queries the orchestrator and reports.
---

# Now Playing — current track inspector

Show the user what the orchestrator currently knows about playback. Pure read.

## Query the WebSocket once

```bash
ssh <pi-host> "timeout 3 ~/now-playing/pi/.venv/bin/python -c \"
import asyncio, aiohttp, json
async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect('http://localhost:8080/ws') as ws:
            async for msg in ws:
                d = json.loads(msg.data)
                if d.get('type') == 'now_playing':
                    p = d['payload']
                    print(f'source={p.get(\\\"source\\\")} state={p.get(\\\"state\\\")}')
                    print(f'title={p.get(\\\"title\\\")!r}')
                    print(f'artist={p.get(\\\"artist\\\")!r}')
                    print(f'album={p.get(\\\"album\\\")!r}')
                    print(f'track={p.get(\\\"track_position\\\")!r} side={p.get(\\\"side\\\")!r} match={p.get(\\\"match_method\\\")}')
                    return
asyncio.run(main())
\""
```

Present the output as:

> **Now playing:** {artist} — {title} ({album}, side {side})
> Source: {source} · Match: {match_method}

If no payload arrives within 3 seconds, the orchestrator is likely idle (`state=STOPPED`) or down — recommend `nowplaying-diagnose`.

## Optional: last 5 minutes of recognitions

```bash
ssh <pi-host> 'journalctl -u nowplaying-orchestrator --since "5 minutes ago" --no-pager | grep "recognize:"' | tail -10
```
