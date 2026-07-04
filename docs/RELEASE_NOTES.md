# Release notes

## v1-local-product-dogfood-pass (2026-07-04)

Commit: `56b35a86cbdfaecbe4b56c84071f411f96cc4388` (+ hygiene docs)

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
