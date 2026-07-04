#!/usr/bin/env bash
# Install purple_halo as a persistent user service on Atlas.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_SRC="$ROOT/systemd/purple-halo-operator.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_DST="$UNIT_DIR/purple-halo-operator.service"

mkdir -p "$UNIT_DIR" "$ROOT/project_memory/runtime"
# Rewrite WorkingDirectory/paths for this checkout if needed.
sed "s|/home/sketch/Projects/purple_halo|$ROOT|g" "$UNIT_SRC" > "$UNIT_DST"

systemctl --user daemon-reload
systemctl --user enable purple-halo-operator.service
systemctl --user restart purple-halo-operator.service

# Allow user services without interactive login (boot start).
if command -v loginctl >/dev/null 2>&1; then
  loginctl enable-linger "$USER" >/dev/null 2>&1 || true
fi

sleep 1
systemctl --user --no-pager --full status purple-halo-operator.service || true
echo
echo "UI: http://127.0.0.1:8765/"
echo "Manage: systemctl --user status|restart|stop purple-halo-operator.service"