# Now Playing — Claude Code skills

This directory contains project-local skills that activate automatically when you use Claude Code with this repo open. They wrap the operational knowledge needed to install, troubleshoot, and inspect the system.

## Install

The install is staged across one coordinator and six phase skills. You don't invoke them by name — describe what you're doing and the right skill fires.

| Skill | When it fires |
|-------|---------------|
| `nowplaying-setup` | "Help me install this." Coordinator. Figures out which phase you're in and hands off. |
| `nowplaying-setup-hardware` | "Wire the audio chain / connect the turntable / connect the display." |
| `nowplaying-setup-pi` | "Flash the SD card / first-time Pi setup / set the hostname." |
| `nowplaying-setup-sonos` | "Configure my Sonos zone / set line-in source." |
| `nowplaying-setup-accounts` | "Get a Discogs token." |
| `nowplaying-setup-install` | "Install the backend / run uv sync / sync my catalog." |
| `nowplaying-setup-services` | "Install the systemd services / open the kiosk." |

The phases are sequential. The coordinator detects state (does the repo exist on the Pi? is the catalog synced? are services running?) and routes you to the right phase — even if you come back to the install mid-way.

## Operations

| Skill | When it fires |
|-------|---------------|
| `nowplaying-troubleshoot` | Service won't start, no recognition, wrong track shown, audio device missing. |
| `nowplaying-diagnose` | Read-only inspection — recent recognitions, capture levels, promotion progress. |
| `nowplaying-status` | "What's playing right now?" |

## Conventions assumed

- You have a host alias `nowplaying-pi` in `~/.ssh/config` (the setup skills walk you through creating one).
- You can `ssh nowplaying-pi` from your Mac with key-based auth — no password prompt.
- Passwordless sudo on the Pi.

## How they work

Each skill is a `SKILL.md` file with YAML frontmatter. The `description` field tells Claude when to activate the skill — it watches user phrasing and triggers when the situation matches. You don't invoke them explicitly; just describe what you're trying to do.

Examples:

- "Help me install this on a fresh Pi" → `nowplaying-setup` activates and routes you to the right phase.
- "Wire up the turntable" → `nowplaying-setup-hardware`.
- "Get me a discogs token" → `nowplaying-setup-accounts`.
- "The orchestrator service won't start" → `nowplaying-troubleshoot`.
- "What's playing right now?" → `nowplaying-status`.

## Editing skills

Skills are plain markdown. Edit and commit — they reload on the next Claude Code session. Keep the `description` field tight; vague descriptions misfire (triggering on unrelated phrases or missing intended ones).
