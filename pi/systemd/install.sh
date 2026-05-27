#!/usr/bin/env bash
# Install (or re-install) the Now Playing systemd units.
#
# Usage:
#   sudo bash pi/systemd/install.sh
#
# Detects the invoking user (via $SUDO_USER) and substitutes their username +
# home directory into the unit templates before installing into
# /etc/systemd/system. Safe to re-run after editing units.
#
# Override the target user with: sudo NOWPLAYING_USER=<name> bash pi/systemd/install.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNITS=(nowplaying-orchestrator.service nowplaying-kiosk.service)

if [ "$(id -u)" -ne 0 ]; then
    echo "Re-running with sudo..."
    exec sudo --preserve-env=NOWPLAYING_USER bash "$0" "$@"
fi

target_user="${NOWPLAYING_USER:-${SUDO_USER:-}}"
if [ -z "$target_user" ]; then
    echo "Could not determine target user." >&2
    echo "Set NOWPLAYING_USER=<name> or run via sudo from a non-root shell." >&2
    exit 1
fi
target_home="$(getent passwd "$target_user" | cut -d: -f6)"
if [ -z "$target_home" ] || [ ! -d "$target_home" ]; then
    echo "Could not resolve home directory for user '$target_user'." >&2
    exit 1
fi

echo "Installing units for user=$target_user home=$target_home"

for unit in "${UNITS[@]}"; do
    src="$SCRIPT_DIR/$unit"
    dst="/etc/systemd/system/$unit"
    if [ ! -f "$src" ]; then
        echo "missing unit file: $src" >&2
        exit 1
    fi
    # Substitute placeholders. Using `|` as the sed delimiter so $target_home's
    # slashes don't need escaping.
    sed \
        -e "s|__NOWPLAYING_USER__|$target_user|g" \
        -e "s|__NOWPLAYING_HOME__|$target_home|g" \
        "$src" > "$dst"
    chmod 0644 "$dst"
    echo "installed $dst"
done

systemctl daemon-reload

for unit in "${UNITS[@]}"; do
    systemctl enable "$unit"
done

# Merge kiosk-relevant env vars (currently XCURSOR_SIZE / XCURSOR_THEME)
# into the labwc compositor's environment file. Required because the Pi
# runs Wayland — the compositor (not Chromium) renders the cursor, so
# XCURSOR_* must be in labwc's env, not the kiosk service's. The merge
# is idempotent: re-running this script replaces any existing
# KEY=... line instead of appending duplicates. For the change to take
# effect the labwc session must restart — easiest is `sudo reboot`,
# or log out + back in of the desktop session.
labwc_src="$SCRIPT_DIR/../labwc/environment"
labwc_dir="$target_home/.config/labwc"
labwc_dst="$labwc_dir/environment"
if [ -f "$labwc_src" ]; then
    install -d -o "$target_user" -g "$target_user" -m 0755 "$labwc_dir"
    if [ ! -f "$labwc_dst" ]; then
        install -o "$target_user" -g "$target_user" -m 0644 "$labwc_src" "$labwc_dst"
    else
        # For each KEY=VALUE line in our source, replace any existing
        # KEY=... line in the destination; if absent, append it.
        tmp="$(mktemp)"
        cp "$labwc_dst" "$tmp"
        while IFS= read -r line || [ -n "$line" ]; do
            # Skip blank lines and comments
            case "$line" in
                ''|\#*) continue ;;
            esac
            key="${line%%=*}"
            # Validate key looks like a shell-ish identifier
            case "$key" in
                ''|*[!A-Za-z0-9_]*) continue ;;
            esac
            if grep -qE "^[[:space:]]*${key}=" "$tmp"; then
                # Replace existing line (use | as sed delimiter)
                sed -i.bak -E "s|^[[:space:]]*${key}=.*|${line}|" "$tmp"
                rm -f "${tmp}.bak"
            else
                printf '%s\n' "$line" >> "$tmp"
            fi
        done < "$labwc_src"
        install -o "$target_user" -g "$target_user" -m 0644 "$tmp" "$labwc_dst"
        rm -f "$tmp"
    fi
    echo "installed cursor env in $labwc_dst (reboot or restart labwc session for it to take effect)"
fi

pkill -f "python -m nowplaying.main" 2>/dev/null || true
sleep 1

systemctl restart nowplaying-orchestrator.service
if ! systemctl restart nowplaying-kiosk.service; then
    echo ""
    echo "note: kiosk service failed to start — typically because no graphical session"
    echo "      is active or chromium is not installed. The service is enabled and will"
    echo "      try again on next boot/graphical login. Inspect with:"
    echo "        systemctl status nowplaying-kiosk"
    echo "        journalctl -u nowplaying-kiosk -n 30"
fi

echo ""
echo "Services installed. Check status with:"
echo "  systemctl status nowplaying-orchestrator nowplaying-kiosk"
echo "Tail logs with:"
echo "  journalctl -u nowplaying-orchestrator -f"
