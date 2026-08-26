# CLAUDE.md

Project-level instructions for AI assistants working in this repository.

## What this project is

A Raspberry Pi kiosk that displays "now playing" info for a vinyl turntable, driven by Sonos line-in audio piped through a USB capture device, identified via ShazamIO, enriched against the user's Discogs collection when available, or auto-discovered via MusicBrainz when the album isn't in their collection. Two services:

- **`pi/`** — Python orchestrator (FastAPI + asyncio). Owns audio capture, recognition cascade, Sonos integration, WebSocket broadcast.
- **`kiosk/`** — React + Vite frontend. Renders the now-playing screen.

See [README.md](README.md) for the user-facing overview and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the technical reference.

## Bundled skills

This repo ships project-local Claude Code skills in `.claude/skills/`. When the user describes a matching situation, the skills activate automatically — you don't invoke them by name:

| Skill | Activates when the user is… |
|-------|-----------------------------|
| `nowplaying-setup` | Installing on a fresh Pi |
| `nowplaying-troubleshoot` | Reporting the kiosk isn't working |
| `nowplaying-diagnose` | Asking what the system is doing right now (read-only) |
| `nowplaying-status` | Asking what's currently playing |

If the user's request fits one of these, the skill content has the runbook — follow it.

## Conventions

- **The venv is at `pi/.venv/`**, not the repo root. Invoke its binaries directly.
- **Testing:** `pi/.venv/bin/pytest pi/tests/` for backend; `cd kiosk && npm test` for frontend. Both must be green before merge.
- **Linting / dead-code:** pre-commit hooks run `skylos` (dead code), `fallow` (complexity), `ruff` (style). Don't bypass with `--no-verify`. If a hook fires, fix the underlying issue or add a suppression with an inline rationale on the same line.
- **Suppressions:** every `# skylos: ignore`, `# fallow-ignore`, `# noqa`, `# type: ignore` must carry an inline rationale explaining why the rule's *application* (not its finding) is wrong here. The canonical form is an em-dash suffix on the same line: `# skylos: ignore SKY-D216 — url built from hardcoded constant; host is not user-controlled`. The `# Why:` prefix form is also accepted. Neither form may be omitted.
- **Commits:** never use `--no-verify`, never amend already-pushed commits, never add `Co-Authored-By` attribution.
- **No journey documents.** This repo intentionally does not contain feature-history docs, design rationale narratives, or session logs. Code + tests + README + ARCHITECTURE + INSTALL are the artifacts. If you generate planning files while working, keep them under `.claude/` (already gitignored) — not committed.

## Hardware-in-the-loop reality

The orchestrator's correctness depends on real audio flowing through a real USB capture device with real Shazam rate limits and a real Sonos UPnP subscription. Unit tests cover code correctness; only live deployment to a Pi covers feature correctness. When the user reports a bug that involves the recognition cascade timing, idle clock, or Sonos events, expect the verification step to involve SSH'ing to the Pi and watching `journalctl -u nowplaying-orchestrator -f`.

The `nowplaying-troubleshoot` and `nowplaying-diagnose` skills cover the operational side of this.

## What to avoid

- Don't add backwards-compatibility shims unless the user explicitly asks.
- Don't write multi-paragraph docstrings or planning narratives.
- Don't introduce abstractions ahead of need; three similar lines beats a premature factory.
- Don't mock external services in tests if a real integration test covers the same path — see existing tests for the pattern.
