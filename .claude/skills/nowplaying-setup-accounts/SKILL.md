---
name: nowplaying-setup-accounts
description: Use when the user needs to generate the third-party credentials Now Playing requires — currently just a Discogs personal access token. Triggers on phrases like "set up my discogs account", "get a discogs token", "discogs api key", "accounts", "credentials", "what api keys do I need".
---

# Now Playing — accounts and credentials

Now Playing runs **without any third-party credentials**. A Discogs personal access token is **recommended** — it gives the kiosk canonical pressing metadata, local art scans, and the `/identify` browse flow — but if you skip this phase the kiosk still gets album art, tracklist, side timer, and BEST GUESS via MusicBrainz auto-discovery on the no-Discogs path. This skill walks the user through generating the token. It isn't installed anywhere yet — it gets pasted into `pi/.env` during the next phase.

## Why Discogs is recommended (not required)

The kiosk's identity layer is strongest when it has the user's own Discogs collection. When a track is recognized, the orchestrator looks up which **release** in the user's collection it belongs to — that's where canonical album art, tracklist, year, label, and pressing info come from. With a Discogs token, you also get the `/identify` browse flow and your own scanned cover art.

Without a token, the orchestrator falls back to MusicBrainz: it auto-discovers the release in the background, persists it to `pi/data/discovered.sqlite`, and the kiosk still shows album art, tracklist, side timer, and BEST GUESS. The fingerprint cascade still works (MBID-keyed instead of Discogs-keyed).

You don't need a *large* collection — even a handful of releases gets the system running so you can see it identify those records. If you'd rather skip Discogs entirely, that's supported — just tell Claude.

## Step 1: Have a Discogs account

If the user doesn't already have one, send them to <https://www.discogs.com/users/create>. Free. Optionally add some releases to their collection — minimum one to actually see recognitions, but the install can proceed with an empty collection.

## Step 2: Generate a personal access token

1. Go to <https://www.discogs.com/settings/developers>.
2. Click **Generate new token** (under "Developers" / "Personal access token").
3. Copy the token immediately — Discogs shows it once. It looks like a long alphanumeric string.

That's the entire OAuth — Discogs personal tokens authenticate the same user account that generated them, no OAuth dance required.

## Step 3: Confirm the token works

The token will be tested for real during `nowplaying-setup-install`'s catalog sync step. If the user wants to sanity-check it now, a one-line curl works:

```bash
curl -s -H "Authorization: Discogs token=<paste-token-here>" \
  "https://api.discogs.com/oauth/identity"
```

A working token returns JSON containing `username` and `id`. A bad token returns `{"message": "You must authenticate to access this resource."}`.

## Step 4: Hand the token to the next phase

Don't write it anywhere yet. The next phase (`nowplaying-setup-install`) creates `pi/.env` and pastes the token in — keeping it scoped to that file is the right place.

If the user wants to write it down briefly, the macOS Notes app or a password manager is fine; **don't paste it into chat history** or anywhere that gets committed to git.

## You're done with this phase when

- The user has a Discogs personal access token they can paste, **or** they've decided to skip Discogs and run on the MusicBrainz-only path.
- (Optional) The curl identity check returned their username.

## Next

Ask Claude to **"install the software"** → `nowplaying-setup-install`.

### Skip option

If the user wants to skip Discogs entirely, hand off straight to `nowplaying-setup-install` without setting `DISCOGS_TOKEN` in `.env`. The orchestrator tolerates a missing `discogs.sqlite` and routes everything through the MusicBrainz discovery path.

## When to switch skills

- The user wants to skip Discogs entirely → supported. Move on to `nowplaying-setup-install` and leave `DISCOGS_TOKEN` unset; the kiosk will use MusicBrainz auto-discovery for album-art, tracklist, side timer, and BEST GUESS.
- They get a 401 from the identity check with a freshly-generated token → re-generate the token and try again; tokens can be copied with leading/trailing whitespace which the API rejects.

## What this skill is NOT

There are no other accounts you need. Discogs is the only optional credential. Album art is fetched live from MusicBrainz / Cover Art Archive, which doesn't require an account.
