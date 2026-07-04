# purple_halo Operator Runbook

## Primary path: Simple UI

Open **http://127.0.0.1:8765/** (or the port shown when you installed into a target repo).

The page shows which repo this instance controls, the UI URL, service state, and report file path at the top.

### First-time flow

1. **Repo** — confirm the controlled repo path. To add purple_halo elsewhere, enter a path and **Install**. The install result shows **UI URL**, port, and systemd unit (also printed by `ph_cli install`).
2. **Goal** — point at a goal file; it is copied to `project_goals.md`.
3. **How often** — e.g. every 2 hours for 10 days; optionally stop when the goal is achieved.
4. **Play** — start auto-run; **Pause** stops it; **Run once now** triggers a single cycle.
5. **Report** — each run appends `MMDDYY HHMM` + summary to `RUN_REPORT.md`.

Stops when the goal is achieved or the day limit elapses, whichever comes first.

### CLI (same actions)

```bash
python3 scripts/ph_cli.py install /path/to/repo --goal /path/to/goals.md
cd /path/to/repo
python3 scripts/ph_cli.py frequency --every 2h --for-days 10 --until-goal
python3 scripts/ph_cli.py goal /path/to/goals.md
python3 scripts/ph_cli.py play
python3 scripts/ph_cli.py pause
python3 scripts/ph_cli.py status
python3 scripts/ph_cli.py report
```

### Service

```bash
# install or update (once per machine / after pull)
bash scripts/install_purple_halo.sh

systemctl --user status purple-halo-operator.service
systemctl --user restart purple-halo-operator.service
```

If the UI is unreachable, restart the service once it returns.

---

## Secondary: Engineering console

**http://127.0.0.1:8765/advanced.html** — debugging and engine inspection only.

Use when you need hold/ledger/budget detail, regression diagnostics, schedule/budget edits, service restart, or self-checks. Daily operation should stay on the Simple UI.

### Self-checks

Engine console → **Run health tests**, or:

```bash
python3 scripts/operator_api.py --self-check
curl -s http://127.0.0.1:8765/api/status/release-gate | python3 -m json.tool
```

### Hold / repair (engineering context)

| Mode | Meaning |
|------|---------|
| `verify_only` | Healthy; no implementation |
| `repair` | Regression; repair work only |

Pause/resume in the engineering console maps to the same runtime state as Simple UI **Pause** / **Play**.