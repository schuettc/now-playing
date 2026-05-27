---
name: nowplaying-setup-pi
description: Use when the user needs to prepare a fresh Raspberry Pi for Now Playing — flashing Pi OS, setting the hostname, enabling SSH, installing base system packages. Triggers on phrases like "prepare my pi", "flash the sd card", "first time pi setup", "set up the pi", "set the hostname", "enable ssh on the pi".
---

# Now Playing — Pi preparation

Get the Pi into a state where it can run the Now Playing software. This skill ends when you can SSH into the Pi and `apt` is up to date.

## Prerequisites you need to confirm

Ask the user (one at a time, only the ones you can't infer):

1. **Pi model.** Pi 4 or Pi 5. Pi 3 is not supported (USB audio capture is unreliable; recognition CPU is borderline). Pi Zero 2 W is untested.
2. **SD card.** 16 GB minimum, 32 GB recommended. Class 10 or better — the Discogs catalog DB and audio clips get a lot of write traffic.
3. **Working from a Mac, or directly on the Pi?** Mac-driven uses `ssh nowplaying-pi` for everything. Direct-on-Pi means running commands in the Pi's terminal. The steps below note where it matters.

## Step 1: Flash Pi OS

Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/) (download for Mac/Windows/Linux).

In the Imager:

1. **Pick OS:** *Raspberry Pi OS (64-bit)* — Bookworm or later (Trixie works and is what the project is tested against). Avoid Bullseye and earlier; the chromium kiosk path is different.
2. **Pick device:** the SD card.
3. **Click the gear icon** (advanced settings) and set:
   - **Hostname:** `nowplaying-pi` (the convention this project uses — change it if you have a reason, but you'll need to update SSH aliases everywhere).
   - **Enable SSH** with public-key auth. Paste your Mac's public key (`~/.ssh/id_ed25519.pub` or `~/.ssh/id_rsa.pub` — generate with `ssh-keygen -t ed25519` if you don't have one).
   - **Username:** `pi` (the default). Set a strong password as a fallback.
   - **WiFi:** your network credentials.
   - **Locale / timezone:** match your location.
4. **Flash.** This takes 5–10 minutes.

5. Eject the SD card, insert into the Pi, power on, **wait 60–90 seconds** for first-boot expansion.

## Step 2: SSH in

From your Mac:

```bash
ssh pi@nowplaying-pi.local
```

If the host doesn't resolve, the Pi may not be up yet — wait another minute. If it still doesn't resolve, your network may not support mDNS — fall back to finding the Pi's IP in your router's DHCP table.

Add a host alias to make subsequent commands easier. On your Mac, edit `~/.ssh/config`:

```
Host nowplaying-pi
    HostName nowplaying-pi.local
    User pi
    IdentityFile ~/.ssh/id_ed25519
```

Now `ssh nowplaying-pi` works without the `pi@` prefix. The repo's skills assume this alias.

## Step 3: Enable passwordless sudo for `pi`

The systemd installer runs commands as the user. Verify that `sudo` works without a password (the Imager-set defaults usually grant this for the `pi` user):

```bash
ssh nowplaying-pi 'sudo -n true && echo OK'
```

If that prints `OK`, you're good. If it asks for a password, add a sudoers drop-in:

```bash
ssh nowplaying-pi 'echo "pi ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/010-pi-nopasswd'
```

Use a different username substitution if your installer user is not `pi`.

## Step 4: Update apt and install base packages

```bash
ssh nowplaying-pi 'sudo apt update && sudo apt install -y git curl chromium ca-certificates'
```

Notes:

- On older Pi OS images the chromium package is `chromium-browser`. The kiosk service looks for `/usr/bin/chromium` — if you installed `chromium-browser`, symlink: `sudo ln -sf /usr/bin/chromium-browser /usr/bin/chromium`.
- `ca-certificates` is needed for the Discogs / MusicBrainz HTTPS calls to validate.

## Step 5: Confirm the Pi can reach the internet AND your Sonos zone

```bash
ssh nowplaying-pi 'ping -c1 1.1.1.1 && ping -c1 raw.githubusercontent.com'
```

If internet works but you can't reach your Sonos zone IP, you have a VLAN/multicast problem — flag it now, address in `nowplaying-setup-sonos`.

## You're done with this phase when

- `ssh nowplaying-pi` works from your Mac without a password prompt (key-based auth).
- `sudo -n true` succeeds (passwordless sudo).
- `which chromium` returns `/usr/bin/chromium`.
- `ping -c1 1.1.1.1` succeeds.

## Next

Ask Claude to **"configure my sonos zone"** → `nowplaying-setup-sonos`.

## When to switch skills

- Pi won't boot, no display, no network → upstream Raspberry Pi support, not this project.
- `ssh nowplaying-pi.local` doesn't resolve but you can find the Pi by IP → fix your local DNS / mDNS; the rest of the install can use IP, but commands in this repo assume the hostname alias.
- Audio device wiring not done yet → `nowplaying-setup-hardware`.
