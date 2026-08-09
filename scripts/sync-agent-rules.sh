#!/usr/bin/env bash
# Regenerate generated agent-rule outputs from canonical AGENTS.md.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f AGENTS.md ]]; then
  echo "ERROR: AGENTS.md missing — cannot sync"
  exit 1
fi

write_short_adapter() {
  local name="$1"
  cat > "$name" <<'EOF'
# ADAPTER — see AGENTS.md

<!-- AGENT_CONTRACT_VERSION: universal-2026-Q2-v7 -->

Canonical contract: [`AGENTS.md`](AGENTS.md) — MCP-first routing, deferred tool loading, Mimir recall/outcomes, Architect for risky direct changes, trace/eval contracts, Lazy Senior Developer, `ponytail:` comments.

Cursor adapters: `.cursor/rules/*.mdc`, `.cursor/skills/`.
EOF
  # Fix adapter title line per file
  case "$name" in
    CLAUDE.md) sed -i '1s/.*/# CLAUDE.md — adapter/' "$name" ;;
    GEMINI.md) sed -i '1s/.*/# GEMINI.md — adapter/' "$name" ;;
  esac
  echo "Wrote $name short adapter"
}

ensure_adapter() {
  local name="$1"
  if [[ -L "$name" ]]; then
    rm -f "$name"
    write_short_adapter "$name"
  elif [[ -f "$name" ]] && [[ $(wc -l < "$name" | tr -d ' ') -le 10 ]]; then
    echo "$name is short pointer — leaving as-is"
  elif [[ -f "$name" ]]; then
    rm -f "$name"
    write_short_adapter "$name"
  else
    write_short_adapter "$name"
  fi
}

# --- .cursorrules stub ---

cat > .cursorrules <<'EOF'
# GENERATED — Legacy compatibility stub. Do not edit.
# Canonical rules: AGENTS.md
# Regenerate: scripts/sync-agent-rules.sh

See AGENTS.md and .cursor/rules/00-core-contract.mdc (pointer only).
EOF
echo "Wrote .cursorrules stub"

# --- Cross-agent adapters (short pointers only) ---

ensure_adapter CLAUDE.md
ensure_adapter GEMINI.md

# --- Cursor pointer rule (generated; not a full duplicate) ---

mkdir -p .cursor/rules
cat > .cursor/rules/00-core-contract.mdc <<'EOF'
---
description: Universal core contract pointer
alwaysApply: true
---

Canonical project rules are in `AGENTS.md`.

Do not duplicate the full rule contract here.

Load deeper guidance only when relevant:

- Verification → `.cursor/rules/01-verification.mdc`, `docs/verification-harness.md`
- Implementation/lazy coder/ponytail → `.cursor/rules/02-implementation.mdc`
- Approval/security → `.cursor/rules/03-approval-gates.mdc`
- Mimir → `.cursor/rules/04-mimir.mdc`, `docs/mimir-tools.md`
- Serena → `.cursor/rules/06-serena.mdc`, `docs/serena-tools.md`
- Code navigation substrate → `.cursor/rules/09-code-navigation.mdc`, `docs/code-navigation-production.md`, `docs/codebase-navigation-plan.md`
- Runtime contracts → `contracts/*.schema.json`, `docs/orchestration-runtime.md`
- Cursor shell entrypoint → `scripts/cursor_session.py`, `docs/usage/mcp_cursor.md`
- Project continuity → `docs/project-continuity.md`
- Values layer → `COMMANDMENTS_OF_THE_CODE.md`
EOF
echo "Wrote .cursor/rules/00-core-contract.mdc pointer"

# --- Remove legacy monolithic mdc if present ---

if [[ -f .cursor/rules/00-project-contract.mdc ]]; then
  rm -f .cursor/rules/00-project-contract.mdc
  echo "Removed legacy .cursor/rules/00-project-contract.mdc"
fi

# --- Run audit ---

echo "---"
if [[ -x scripts/audit-agent-rules.sh ]]; then
  bash scripts/audit-agent-rules.sh
else
  echo "audit-agent-rules: skipped (optional audit script not installed)"
fi
