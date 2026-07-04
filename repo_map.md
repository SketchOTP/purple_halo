# Repo Map

Living navigation map for **purple_halo** (`/home/sketch/Projects/purple_halo`).

## Purpose

Minimal autonomous product-build loop: inspect goal/status/repo, research, plan one step, execute, verify, persist state, repeat.

## Operator product (primary)

| Path | Role |
|------|------|
| `operator_ui/index.html` | **Primary** Simple UI — install, goal, frequency, play, report |
| `operator_ui/app.js` | Simple UI client |
| `scripts/ph_cli.py` | CLI mirror of Simple UI actions |
| `scripts/operator_api.py` | Local API (`/api/simple/*` + engineering routes) |
| `RUN_REPORT.md` | Human-readable run log |

Engineering console (secondary): `operator_ui/advanced.html`

## Loop entry points

| Path | Role |
|------|------|
| `scripts/purple_halo_loop.py` | Main loop entrypoint (`run`, `status`, `--self-check`) |
| `scripts/loop_research.py` | Online research step |
| `scripts/loop_plan.py` | Bounded next-step planner |
| `scripts/loop_execute.py` | Plan executor |
| `scripts/loop_verify.py` | Cycle verifier |
| `scripts/loop_state.py` | Continuity state load/save |
| `scripts/loop_schedule.py` | Operator schedule reader |
| `scripts/verify-loop.sh` | Loop verification harness |

## State and artifacts

| Path | Role |
|------|------|
| `contracts/loop-state.schema.json` | Loop continuity JSON schema |
| `contracts/schedule.schema.json` | Minimal operator schedule schema |
| `project_memory/runtime/loop_state.json` | Latest loop continuity state |
| `project_memory/runtime/loop_cycles/cycle_NNNN/` | Per-cycle research/plan/execution/verification JSON |
| `project_memory/runtime/schedule.default.json` | Default schedule (disabled) |
| `project_memory/runtime/schedule.json` | Active schedule override (optional) |

## Repo-truth docs

- `project_goals.md` — durable mission
- `project_status.md` — current loop/repo status
- `project_learning/active.md` — validated lessons
- `AGENTS.md` — agent contract

## Governance substrate (installed kernel)

- `scripts/cursor_session.py` — governed execution adapters
- `scripts/session_orchestrator.py`, `scripts/hook_runner.py` — routing/hooks
- `.cursor/rules/`, `.cursor/hooks.json` — repo-local enforcement
- `contracts/` — trace, eval, resume, hook schemas (kernel)

## Commands

```bash
python3 scripts/purple_halo_loop.py run
bash scripts/verify-loop.sh
```

## Loop modules

Loop stack (product):

- `scripts/purple_halo_loop.py` — cycle entrypoint
- `scripts/loop_backlog.py` — goal backlog queue
- `scripts/loop_plan.py` — planner
- `scripts/loop_execute.py` — executor
- `scripts/loop_verify.py` — verifier
- `scripts/loop_schedule.py` / `loop_runner.py` — scheduled runs
- `project_memory/runtime/goal_backlog.json` — product work queue
