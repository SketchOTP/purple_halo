# Release notes

## v1-local-product-dogfood-pass (2026-07-04)

Commit: `63021f82e985fd46a624a34fa5635f794c2f3d66` (tag `v1-local-product-dogfood-pass`; full product body + hygiene + dogfood fixes)

### Cross-repo Simple UI dogfood — complete

Validated in browser (Simple UI `/`, not engineering console) on three non-self repos:

| Repo | UI | Service |
|------|-----|---------|
| agent | http://127.0.0.1:8854/ | `purple-halo-agent.service` |
| mimir | http://127.0.0.1:8860/ | `purple-halo-mimir.service` |
| atlas | http://127.0.0.1:8827/ | `purple-halo-atlas.service` |

### Fixes in this release

- **Service/port discovery** — per-repo Simple UI shows correct systemd unit (was always `purple-halo-operator.service`).
- **Install output** — `ph_cli install` / `install_to_repo.sh` now print `UI: http://127.0.0.1:PORT/`.
- **Report failure formatting** — `RUN_REPORT.md` failure lines use `stop_reason` / `stop_detail` instead of truncated JSON blobs.

### Self-product (this checkout)

- UI: http://127.0.0.1:8765/
- Service: `purple-halo-operator.service`

### Known issue (non-blocking)

One **historical** pre-fix truncated failure line remains in `/home/sketch/Projects/agent/RUN_REPORT.md`:

```text
070426 1632 Failed: {   "stopped": true,   "stop_reason": "no_executable_work", ...
```

New failures format as `Failed (no_executable_work; backlog_empty)`. No engine change required.

### Packaging note

Self service `ExecStartPre` health check can exceed 60s on a loaded host. Unit `TimeoutStartSec` is **180s** so install/update restart succeeds.

### Update path verified (2026-07-04)

Documented path `bash scripts/install_purple_halo.sh --update` completed **exit 0** with no manual intervention:

| Phase | Observed |
|-------|----------|
| Full script | ~401s wall |
| `operator_runtime.py --health` (in script) | ~61s |
| All embedded self-checks | pass |
| systemd `ExecStartPre` health | ~70s (17:18:53 → 17:20:03) |
| `TimeoutStartSec=180` | sufficient (no timeout) |

After systemd reports **active**, HTTP on **8765** may take **~1–2 min** more: `operator_service.py` runs `startup_health_checks()` again before binding the port. This is expected v1 behavior, not a timeout failure.

Post-update smoke: `operator_api --self-check` PASS · `ph_cli status`/`report` OK · HTTP 200 at http://127.0.0.1:8765/
