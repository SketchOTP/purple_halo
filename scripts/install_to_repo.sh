#!/usr/bin/env bash
# Install purple_halo into another repo (project mode: build toward a goal).
# Usage:
#   bash scripts/install_to_repo.sh /path/to/repo
#   bash scripts/install_to_repo.sh /path/to/repo --goal /path/to/goals.md
#   bash scripts/install_to_repo.sh /path/to/repo --goal goals.md --no-service
set -euo pipefail

SOURCE="$(cd "$(dirname "$0")/.." && pwd)"
DEST=""
GOAL=""
NO_SERVICE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --goal)
      GOAL="${2:-}"
      shift 2
      ;;
    --no-service)
      NO_SERVICE=1
      shift
      ;;
    -h|--help)
      sed -n '2,7p' "$0"
      exit 0
      ;;
    *)
      if [[ -z "$DEST" ]]; then
        DEST="$1"
        shift
      else
        echo "unexpected arg: $1" >&2
        exit 2
      fi
      ;;
  esac
done

if [[ -z "$DEST" ]]; then
  echo "usage: bash scripts/install_to_repo.sh /path/to/repo [--goal /path/to/goals.md] [--no-service]" >&2
  exit 2
fi

DEST="$(mkdir -p "$DEST" && cd "$DEST" && pwd)"
echo "== purple_halo install into repo =="
echo "source: $SOURCE"
echo "dest:   $DEST"

if [[ "$DEST" == "$SOURCE" ]]; then
  echo "dest is this purple_halo checkout; use scripts/install_purple_halo.sh for self-install" >&2
  exit 2
fi

copy_tree() {
  local src="$1"
  local dst="$2"
  mkdir -p "$dst"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete --exclude '__pycache__' --exclude '*.pyc' "$src/" "$dst/"
  else
    rm -rf "$dst"
    mkdir -p "$(dirname "$dst")"
    cp -a "$src" "$dst"
  fi
}

copy_tree "$SOURCE/scripts" "$DEST/scripts"
copy_tree "$SOURCE/operator_ui" "$DEST/operator_ui"
copy_tree "$SOURCE/config" "$DEST/config"
mkdir -p "$DEST/systemd" "$DEST/docs" "$DEST/project_memory/runtime"

if [[ -d "$SOURCE/systemd" ]]; then
  cp -a "$SOURCE/systemd/." "$DEST/systemd/" 2>/dev/null || true
fi

# Project-mode schedule: interval-ready, not production freeze.
cat > "$DEST/project_memory/runtime/schedule.json" <<'JSON'
{
  "enabled": false,
  "timezone": "UTC",
  "schedule_kind": "interval",
  "every_hours": 2,
  "for_days": null,
  "until_goal_achieved": true,
  "campaign_started_at": null,
  "runs": [],
  "max_runs_per_day": 24,
  "mode": "project_mode",
  "cheap_default": true,
  "monthly_token_ceiling": 500000,
  "production_candidate_operations": false,
  "architecture_freeze": false,
  "goal_delivery_mode": true,
  "auto_pause_conditions": [
    "monthly_token_ceiling",
    "operator_pause",
    "budget_guard"
  ]
}
JSON

cat > "$DEST/project_memory/runtime/schedule_run_history.json" <<'JSON'
{
  "attempts": [],
  "sequence": [],
  "autonomous_allowed": false,
  "stop_classification": "",
  "stop_reason": "",
  "production_hold_mode": false,
  "production_candidate_operations": false,
  "production_candidate": false,
  "live_soak_passed": false,
  "goal_delivery_mode": true,
  "feature_freeze": false,
  "architecture_freeze": false
}
JSON

if [[ -f "$SOURCE/config/templates/cost_policy.json" ]]; then
  cp "$SOURCE/config/templates/cost_policy.json" "$DEST/project_memory/runtime/cost_policy.json"
else
  cat > "$DEST/project_memory/runtime/cost_policy.json" <<'JSON'
{
  "budget_mode": "cheap_default",
  "allow_expensive_execution": false
}
JSON
fi

if [[ -n "$GOAL" ]]; then
  if [[ ! -f "$GOAL" ]]; then
    echo "goal file not found: $GOAL" >&2
    exit 2
  fi
  cp "$GOAL" "$DEST/project_goals.md"
  echo "goal: $DEST/project_goals.md (from $GOAL)"
elif [[ -f "$DEST/project_goals.md" ]]; then
  echo "goal: $DEST/project_goals.md (kept)"
else
  cat > "$DEST/project_goals.md" <<'MD'
# Product Goal

Define the product goal for this repository.

## Success criteria

- [ ] Describe the first success criterion
MD
  echo "goal: seeded placeholder project_goals.md (edit or re-run with --goal)"
fi

if [[ ! -f "$DEST/RUN_REPORT.md" ]]; then
  cat > "$DEST/RUN_REPORT.md" <<'MD'
# purple_halo run report

MD
fi

# Lightweight per-repo docs
cat > "$DEST/docs/PURPLE_HALO.md" <<'MD'
# purple_halo operator

Primary UI: open the Simple UI URL printed by install (install → goal → frequency → play → report).

```bash
# CLI equivalent
python3 scripts/ph_cli.py goal /path/to/goals.md
python3 scripts/ph_cli.py frequency --every 2h --for-days 10 --until-goal
python3 scripts/ph_cli.py play
python3 scripts/ph_cli.py pause
python3 scripts/ph_cli.py status
python3 scripts/ph_cli.py report
```

Engineering console (secondary): `/advanced.html` on the same port.

Each run appends one line to `RUN_REPORT.md`:

```text
MMDDYY HHMM Summary of what was done in this run
```
MD

NAME="$(basename "$DEST" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | sed 's/--*/-/g' | sed 's/^-//;s/-$//')"
UNIT="purple-halo-${NAME}.service"
# Stable port in 8766-8865 so it does not collide with the self-product UI on 8765.
PORT=$((8766 + $(printf '%s' "$DEST" | cksum | awk '{print $1 % 100}')))

if [[ "$NO_SERVICE" -eq 0 ]]; then
  UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  mkdir -p "$UNIT_DIR"
  cat > "$UNIT_DIR/$UNIT" <<UNIT
[Unit]
Description=purple_halo operator for ${NAME}
After=network.target

[Service]
Type=simple
WorkingDirectory=${DEST}
Environment=PYTHONPATH=${DEST}/scripts
Environment=MIMIR_ENDPOINT=
ExecStart=/usr/bin/python3 ${DEST}/scripts/operator_service.py --host 127.0.0.1 --port ${PORT}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
UNIT
  systemctl --user daemon-reload
  systemctl --user enable "$UNIT"
  systemctl --user restart "$UNIT"
  if command -v loginctl >/dev/null 2>&1; then
    loginctl enable-linger "$USER" >/dev/null 2>&1 || true
  fi
  cat > "$DEST/project_memory/runtime/install_meta.json" <<JSON
{
  "unit": "${UNIT}",
  "port": ${PORT},
  "ui_url": "http://127.0.0.1:${PORT}/",
  "repo": "${DEST}",
  "repo_name": "${NAME}"
}
JSON
  echo "service: $UNIT (port $PORT)"
  echo "UI: http://127.0.0.1:${PORT}/"
else
  echo "service: skipped (--no-service)"
fi

echo
echo "Install complete."
echo "Open the UI above, then: goal → frequency → Play."
echo "CLI from $DEST: ph_cli frequency / play / report"
