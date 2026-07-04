# purple_halo Admin Handoff (v1)

## What this product is

`purple_halo` is an **installable autonomous repo operator**:

- **Primary surface:** Simple UI at `http://127.0.0.1:8765/` — install → goal → frequency → play → report
- **Secondary surface:** Engineering console at `/advanced.html` — hold, ledger, budget, diagnostics
- Scheduled runs under `cheap_default`; production hold when healthy
- Persistent local user service (`purple-halo-operator.service` on this checkout; per-repo units when installed elsewhere)

Supported operating mode: **`self_product_mode`** for this checkout. Target-repo installs use the same Simple UI flow on their assigned port.

## Install / update

```bash
cd /path/to/purple_halo
bash scripts/install_purple_halo.sh          # fresh or full update
bash scripts/install_purple_halo.sh --update # same path; keeps existing runtime config
bash scripts/install_atlas_service.sh        # service unit only
```

Config templates: `config/templates/schedule.json`, `config/templates/cost_policy.json`  
Runtime config (authoritative): `project_memory/runtime/schedule.json`, `cost_policy.json`

## Runtime files that matter

| Path | Role |
|---|---|
| `project_memory/runtime/schedule.json` | Schedule, ceilings, mode |
| `project_memory/runtime/schedule_run_history.json` | Autonomy, modes, readiness flags, sequence |
| `project_memory/runtime/cost_policy.json` | Budget mode / expensive toggle |
| `project_memory/runtime/cost_accounting.json` | Daily/monthly token estimates |
| `project_memory/runtime/goal_delivery_ledger.json` | Success criteria ledger |
| `project_memory/runtime/production_hold_state.json` | Hold baseline / reopened criteria |
| `project_memory/runtime/schedule_slot_lock.json` | Restart-safe schedule slot claims |
| `project_memory/runtime/service_status.json` | Service up/down/failure |
| `project_memory/runtime/service.log` | Service stdout/stderr |
| `project_memory/runtime/continuity_state.json` | Cross-cycle continuity |
| `project_memory/runtime/service_soak_report.json` | Soak evidence |
| `project_memory/runtime/ui_dogfood_log.json` | UI-only dogfood evidence |

Corrupt JSON is quarantined as `*.corrupt.<timestamp>` by `operator_runtime.load_json_safe`.

## Modes

| Mode | Meaning |
|---|---|
| `self_product_mode` | Supported default; builds/operates purple_halo itself |
| `production_candidate_operations` | Long-run cheap scheduled self operation |
| `goal_delivery_mode` | Work mapped to `project_goals.md` success criteria |
| `production_hold_mode` | Criteria complete: verify-only unless regression |
| `production_freeze_mode` | v1 freeze: bug/repair/packaging only |
| `live_soak_mode` / `live_soak_passed` | Historical soak evidence |
| `ui_only_dogfood` / `ui_operator_ready` | UI is sufficient for daily ops |
| `service_soak_passed` / `local_production_ready` | Unattended service readiness |

## Stop conditions (common)

| Classification | Meaning |
|---|---|
| `verify_only_healthy` | Hold healthy; no implementation |
| `operator_pause` | Operator paused autonomy |
| `repeated_regression` / soak/hold repair classes | Regression auto-pause or repair path |
| `monthly_token_ceiling` | Monthly budget exhausted |
| `budget_blocked` | Cost policy blocked work |
| `slot_already_claimed` | Schedule slot already fired today (restart-safe) |
| `goal_realized` | Pre-hold stop; hold mode uses verify-only instead |

## Service behavior

- Unit: `purple-halo-operator.service` (user systemd)
- Starts on boot (linger enabled by install script)
- Restarts on crash (`Restart=always`)
- Runs operator API/UI on `127.0.0.1:8765`
- Schedule ticker calls `loop_schedule.py --run-due` periodically
- Slot lock prevents duplicate slot execution after restart

```bash
systemctl --user status|restart|stop purple-halo-operator.service
journalctl --user -u purple-halo-operator.service -n 100
# or project_memory/runtime/service.log
```

## Production readiness gates already passed

These are recorded in `schedule_run_history.json` and shown in the release gate:

- operational realization + live soak (historical)
- UI operator ready (`ui_operator_ready`)
- service soak passed (`service_soak_passed`)
- local production ready (`local_production_ready`)
- production freeze active (`production_freeze_mode`)

Release gate API:

```bash
curl -s http://127.0.0.1:8765/api/status/release-gate | python3 -m json.tool
```

`release_ready` is true only when:

- startup health checks pass
- `ui_operator_ready`
- `service_soak_passed`
- `local_production_ready`
- no open hold regressions
- `production_freeze_mode`

## How to extend after freeze

Default allowed work:

1. bug fixes
2. regression repair
3. operator-requested changes
4. packaging/install improvements

Do **not** add:

- new loop capabilities
- target-mode expansion as default
- new proof modes
- non-bug UI surface expansion

## Operator docs

- Daily use (primary): Simple UI + `docs/OPERATOR_RUNBOOK.md`
- Engineering / runtime detail: `docs/ADMIN_HANDOFF.md` + `/advanced.html`