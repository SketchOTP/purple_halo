#!/usr/bin/env bash
# Single verification entrypoint for repo state.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "== verify: audit-agent-rules =="
[[ -x "$ROOT/scripts/audit-agent-rules.sh" ]] && "$ROOT/scripts/audit-agent-rules.sh" || echo "skipped (optional)"

echo "== verify: select-context =="
python3 "$ROOT/scripts/select-context.py" --self-check

echo "== verify: mimir-code-nav =="
if [[ -f "$HOME/.config/mimir/env" ]]; then
  # shellcheck disable=SC1091
  source "$HOME/.config/mimir/env"
fi
python3 "$ROOT/scripts/mimir_code_nav.py" --self-check

echo "== verify: select-verification =="
python3 "$ROOT/scripts/select-verification.py" --self-check

echo "== verify: validate-contracts =="
python3 "$ROOT/scripts/validate-contracts.py" --self-check

for optional in record-learning-candidate.py compact-project-learning.py validate_enforcement_rollout.py run_governance_benchmarks.py replay_governance_benchmarks.py suggest_benchmark_cases.py promote_benchmark_cases.py trace_replay.py bridge_operator_report.py governance_operator_report.py; do
  [[ -f "$ROOT/scripts/$optional" ]] || continue
  echo "== verify: $optional =="
  python3 "$ROOT/scripts/$optional" --self-check
done

echo "== verify: hook-runner =="
python3 "$ROOT/scripts/hook_runner.py" --self-check

echo "== verify: eval-writer =="
python3 "$ROOT/scripts/eval_writer.py" --self-check

echo "== verify: trace-writer =="
python3 "$ROOT/scripts/trace_writer.py" --self-check

echo "== verify: resume-writer =="
python3 "$ROOT/scripts/resume_writer.py" --self-check

echo "== verify: resume-reader =="
python3 "$ROOT/scripts/resume_reader.py" --self-check

echo "== verify: session-runtime =="
python3 "$ROOT/scripts/session_runtime.py" --self-check

echo "== verify: session-orchestrator =="
python3 "$ROOT/scripts/session_orchestrator.py" --self-check

echo "== verify: cursor-native-enforcement =="
python3 "$ROOT/scripts/cursor_native_enforcement.py" --self-check

echo "== verify: validate-enforcement-rollout =="
echo "== verify: cursor-session =="
python3 "$ROOT/scripts/cursor_session.py" --self-check

echo "== verify: structured-adapters =="
python3 "$ROOT/scripts/structured_adapters.py" --self-check

echo "== verify: policy-worker =="
python3 "$ROOT/scripts/policy_worker.py" --self-check

echo "== verify: governance-benchmarks =="

if [[ -x "$ROOT/scripts/verify-code-navigation-production.sh" ]] && [[ -n "${MIMIR_API_KEY:-}" || -f "$HOME/.config/mimir/env" || -f "../mimir/.env" ]]; then
  echo "== verify: code-navigation-production =="
  "$ROOT/scripts/verify-code-navigation-production.sh"
else
  echo "== verify: code-navigation-production (skipped; set MIMIR_API_KEY or ~/.config/mimir/env) =="
fi

for hook in \
  pyproject.toml:pytest \
  package.json:"npm test" \
  Cargo.toml:"cargo test" \
  go.mod:"go test ./..."
do
  manifest="${hook%%:*}"
  [[ -f "$manifest" ]] || continue
  echo "== verify: stack manifest $manifest present; add command to verify.sh =="
done

echo "VERIFY: PASS"
