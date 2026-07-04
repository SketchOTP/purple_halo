#!/usr/bin/env bash
# Verification entrypoint for purple_halo autonomous loop.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
echo "== verify-loop: module self-checks =="
python3 scripts/loop_state.py --self-check
python3 scripts/loop_target_workspace.py --self-check
python3 scripts/loop_research.py --self-check
python3 scripts/loop_plan.py --self-check
python3 scripts/loop_backlog.py --self-check
python3 scripts/loop_work_package.py --self-check
python3 scripts/loop_artifact_inputs.py --self-check
python3 scripts/loop_cost_policy.py --self-check
python3 scripts/loop_economy_proof.py --self-check
python3 scripts/loop_worker_bridge.py --self-check
python3 scripts/loop_worker_decompose.py --self-check
python3 scripts/loop_runtime_path.py --self-check
if [[ -f scripts/loop_dispatch.py ]]; then
  python3 scripts/loop_dispatch.py --self-check
fi
python3 scripts/loop_product_slices.py --self-check
python3 scripts/loop_execute.py --self-check
python3 scripts/loop_verify.py --self-check
python3 scripts/purple_halo_loop.py --self-check
if [[ -f scripts/loop_schedule.py ]]; then
  python3 scripts/loop_schedule.py --self-check
fi
if [[ -f scripts/loop_runner.py ]]; then
  python3 scripts/loop_runner.py --self-check
fi
echo "== verify-loop: continuity state =="
test -f project_memory/runtime/loop_state.json
python3 -c "import json; json.load(open('project_memory/runtime/loop_state.json'))"
echo "VERIFY-LOOP: PASS"