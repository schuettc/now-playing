---
name: nowplaying-setup-hardware
description: Use when the user is wiring up the physical audio chain for Now Playing — connecting their turntable, RIAA preamp, RCA splitter, Sonos line-in, and USB audio capture device, or attaching the kiosk display and touch USB cable. Triggers on phrases like "wire the audio", "connect the turntable", "how do I split the signal", "what cables do I need", "set up the hardware", "connect the display".
---

# Now Playing — hardware wiring

Walk the user through wiring the physical signal chain. This phase is done before any software work — the install steps depend on the USB capture device already being enumerated by the Pi.

## The mental model

Now Playing is a **parallel ear** on the same signal Sonos already plays. Sonos handles the audio for the room. The Pi listens to a duplicate copy of the line-level signal and identifies the track. Nothing about audio routing changes — the kiosk publishes "what's playing" without ever being in the audio path.

```
                       ┌─── Sonos line-in (room audio)
turntable ─► preamp ──┤
                       └─── UFO202 input ──► Pi USB (recognition ear)
                       │
                       (passive RCA Y-splitter or active splitter)
```

## Step 1: Confirm the listening source

Ask: **"What's feeding your Sonos line-in today?"** The most common answer is "a turntable through a phono preamp." But the project works for any line-level source — CD player, tape deck, FM tuner. The wiring below assumes turntable; adapt for other sources.

If the turntable has a **built-in preamp** (some modern decks do), the line-level output goes straight to the splitter — skip the external preamp box. Confirm by checking the turntable's manual or back-panel labeling (look for "PHONO" vs "LINE" output switch).

## Step 2: Get the parts in front of you

| Item | Purpose | Notes |
|------|---------|-------|
| RCA Y-splitter (1 male → 2 female) ×2 | Splits the L + R line-level signal | Passive is fine for short cable runs (<2m total). Active if you hear gain/level changes after splitting. |
| Behringer UFO202 | USB audio capture device | The project's reference device. Any USB stereo input with `max_input_channels >= 2` works; UFO202 is what the default config expects. |
| RCA cables ×2 | Connect splitter outputs | One pair to Sonos, one pair to UFO202. |
| USB-A cable | UFO202 to Pi | UFO202 ships with one. |

Optional but supported:

| Item | Purpose |
|------|---------|
| Kiosk display (HDMI or DSI) | The visible "now playing" screen. AMOLED panels make album art pop; the Waveshare 13.3" FHD AMOLED Touch is what this repo is tuned for, but any HDMI display works. |
| USB-A cable for touch | Waveshare touch display has a **separate** USB cable for touch input. HDMI carries video only; without the touch USB cable, taps do nothing. |

## Step 3: Wire it

1. **Turntable → preamp.** Standard RCA L/R + ground wire. If the turntable has a built-in preamp set to LINE, skip this and use the deck's main output instead.

2. **Preamp output → splitter input.** Use the Y-splitter to fork the L + R signal. You need two splitters total (one per channel), OR a stereo splitter that does both channels in one unit.

3. **Splitter leg 1 → Sonos line-in.** Use whatever cable was already feeding Sonos. This is the unchanged half.

4. **Splitter leg 2 → UFO202 inputs.** UFO202 has RCA L/R inputs labeled INPUTS. Match L→L, R→R.

5. **UFO202 → Pi USB.** Any USB-A port on the Pi. UFO202 is bus-powered; no external supply.

6. **Display (if used) → Pi HDMI.** For Waveshare touch panels: also run a USB-A cable from the display's "Touch" port to a free USB-A port on the Pi.

## Step 4: Verify each connection

These verifications happen before software install — they confirm the kernel sees the hardware, not that the orchestrator works. Run on the Pi (or SSH in):

1. **USB audio device enumerated:**
   ```bash
   lsusb | grep -i "behringer\|codec\|audio"
   ```
   Expect a "Burr-Brown" or "USB Audio" line for the UFO202.

2. **ALSA sees an input device:**
   ```bash
   arecord -l
   ```
   Expect a card line like `card 3: CODEC [USB Audio CODEC], device 0`. Note the card number — install verification will reference this.

3. **(If touch display) HID device enumerated:**
   ```bash
   lsusb
   sudo libinput list-devices | grep -A5 -i touch
   ```
   Expect a device with `Capabilities: touch`. If `lsusb` shows a new HID device but `libinput` doesn't see touch capability, you're missing the `hid_multitouch` kernel module — load it: `sudo modprobe hid_multitouch && echo hid_multitouch | sudo tee -a /etc/modules`.

4. **Sonos line-in produces audible sound when you drop a needle.** Trust your ears here — if the room is silent when the record is playing, your splitter wiring or preamp is wrong, not the kiosk.

## You're done with this phase when

- `arecord -l` lists a CODEC / USB Audio input device.
- Playing a record makes audible sound through the Sonos zone.
- (If touch display) `libinput list-devices` shows a touch-capable device.

## Next

If the Pi isn't flashed yet, or you can't SSH into it, ask Claude to **"prepare my pi"** → `nowplaying-setup-pi`.

If the Pi is already up and reachable, ask Claude to **"configure my sonos zone"** → `nowplaying-setup-sonos`.

## When to switch skills

- Pi isn't reachable, can't SSH → `nowplaying-setup-pi`
- Audio device shows in `lsusb` but not `arecord -l` → `nowplaying-troubleshoot`
- Touch shows in `lsusb` but Chromium doesn't respond to taps → that's a kiosk-level issue, address after services are running (`nowplaying-troubleshoot`)
