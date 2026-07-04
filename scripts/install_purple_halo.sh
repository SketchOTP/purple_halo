#!/usr/bin/env bash
# Fresh install / update of purple_halo on an Atlas-like host (self-product checkout).
# Usage:
#   bash scripts/install_purple_halo.sh
#   bash scripts/install_purple_halo.sh --update
# Install into another repo (project mode):
#   bash scripts/install_to_repo.sh /path/to/repo --goal /path/to/goals.md
#   # or: python3 scripts/ph_cli.py install /path/to/repo --goal /path/to/goals.md
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
UPDATE=0
if [[ "${1:-}" == "--update" ]]; then
  UPDATE=1
fi

echo "== purple_halo install =="
echo "root: $ROOT"

mkdir -p project_memory/runtime config/templates

# Seed config from templates if missing (never overwrite existing on update unless forced).
if [[ ! -f project_memory/runtime/schedule.json ]]; then
  cp config/templates/schedule.json project_memory/runtime/schedule.json
  echo "seeded schedule.json"
elif [[ "$UPDATE" -eq 0 ]]; then
  echo "schedule.json exists (kept)"
fi

if [[ ! -f project_memory/runtime/cost_policy.json ]]; then
  cp config/templates/cost_policy.json project_memory/runtime/cost_policy.json
  echo "seeded cost_policy.json"
else
  echo "cost_policy.json exists (kept)"
fi

if [[ ! -f project_memory/runtime/schedule_run_history.json ]]; then
  cat > project_memory/runtime/schedule_run_history.json <<'JSON'
{
  "attempts": [],
  "sequence": [],
  "autonomous_allowed": true,
  "stop_classification": "",
  "stop_reason": "",
  "production_hold_mode": true,
  "production_candidate_operations": true,
  "production_candidate": true,
  "live_soak_passed": true,
  "goal_delivery_mode": true,
  "feature_freeze": true,
  "architecture_freeze": true
}
JSON
  echo "seeded schedule_run_history.json"
fi

# Runtime health / freeze ensure
PYTHONPATH=scripts python3 scripts/operator_runtime.py --health
PYTHONPATH=scripts python3 scripts/production_freeze.py --ensure >/dev/null

# Self-checks (release gate inputs)
PYTHONPATH=scripts python3 scripts/operator_runtime.py --self-check
PYTHONPATH=scripts python3 scripts/operator_api.py --self-check
PYTHONPATH=scripts python3 scripts/operator_service.py --self-check
PYTHONPATH=scripts python3 scripts/production_freeze.py --self-check
PYTHONPATH=scripts python3 scripts/loop_schedule.py --self-check
PYTHONPATH=scripts python3 scripts/loop_production_hold.py --self-check

# Install/update systemd user service
bash scripts/install_atlas_service.sh

echo
echo "Install complete."
echo "UI: http://127.0.0.1:8765/"
echo "Runbook: docs/OPERATOR_RUNBOOK.md"
echo "Admin handoff: docs/ADMIN_HANDOFF.md"
echo "Release gate: curl -s http://127.0.0.1:8765/api/status/release-gate | python3 -m json.tool"