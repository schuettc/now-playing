# Contributing

Thanks for the interest. Now Playing is a personal-deployment-first project that's open to public use — issues and PRs are welcome, but please read this short doc first so we stay aligned on what's in scope.

## Before opening an issue

**Try the troubleshooting skill first.** If you have Claude Code installed and you're working from a clone of this repo, ask Claude *"The orchestrator service won't start"* (or describe what's broken). The `nowplaying-troubleshoot` skill ships with the repo at [`.claude/skills/`](.claude/skills/) and walks through systematic diagnosis — most installer issues resolve there without needing to file anything.

If that doesn't help, please include in the issue:

1. **What you observed** — what the kiosk showed (or didn't), how you triggered it.
2. **`systemctl status nowplaying-orchestrator`** output.
3. **Recent journal slice**:
   ```bash
   journalctl -u nowplaying-orchestrator --since "1 hour ago" --no-pager
   ```
4. **Pi OS version** (`cat /etc/os-release`).
5. **What changed recently** if anything — fresh install, new hardware, recent `git pull`, etc.

Use the bug template under "New Issue" to structure this.

## PRs welcome for

- **Bug fixes** — especially for things you hit during your own install.
- **Recognition / metadata-source additions** — new lyrics providers, art sources, etc., follow the pattern in `pi/nowplaying/coverart.py`.
- **Kiosk visual polish** — components are under `kiosk/src/components/`, framer-motion + Tailwind.
- **Documentation improvements** — `docs/INSTALL.md` corrections from your install experience are very welcome.

## PRs that need discussion first

Open an issue before sending a PR for:

- **New top-level features** — talk through scope first via an issue so we don't waste your time on a direction that doesn't fit.
- **Changes to the recognition cascade** (`pi/scripts/recognize_proto.py`) — the cascade is load-bearing and benefits from a design conversation before code.
- **Breaking changes** to the WebSocket payload, systemd units, or `.env` schema.

## Development workflow

The maintainer uses an internal feature-tracking process; contributors don't need to follow it.

CI runs Gemini reviews on PRs labeled `plan-review` or `impl-review`. Contributor PRs get human review.

## Code style

- Python: standard library + the deps already in `pi/pyproject.toml`. Type hints where they help readability.
- TypeScript: strict mode is on. Match existing component patterns under `kiosk/src/components/`.
- Comments explain *why*, not *what*. The code already says what.

## License

By contributing you agree your contributions are licensed under the MIT license (see `LICENSE`).
