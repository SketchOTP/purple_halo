# purple_halo

Install purple_halo into any repo, point it at a goal, set how often it runs, press **Play**, and read the report.

## Quick start (v1 operator path)

### 1. Install

**Self (this checkout)** — once per machine or after pull:

```bash
bash scripts/install_purple_halo.sh
```

**Another repo** — copies operator scripts + starts a dedicated service:

```bash
python3 scripts/ph_cli.py install /path/to/your/repo --goal /path/to/goals.md
```

Install output prints **UI URL**, port, and systemd unit. Open that URL next.

| Target | Default UI | Service |
|--------|------------|---------|
| This checkout | http://127.0.0.1:8765/ | `purple-halo-operator.service` |
| Installed repo | `UI: http://127.0.0.1:PORT/` from install | `purple-halo-<repo>.service` |

### 2. Open Simple UI and confirm instance bar

At the top of the page verify:

- **Controlling repo** — path matches your project
- **This UI** — URL you opened (correct port)
- **Service** — running + correct unit name
- **Report file** — `RUN_REPORT.md` path in that repo

### 3. Operate (goal → frequency → play → report)

| Step | Simple UI | CLI (same actions) |
|------|-----------|-------------------|
| Goal | Goal file → **Use this goal** | `python3 scripts/ph_cli.py goal /path/to/goals.md` |
| Frequency | **Save schedule** | `python3 scripts/ph_cli.py frequency --every 2h --for-days 10 --until-goal` |
| Play / Pause | **Play** / **Pause** | `python3 scripts/ph_cli.py play` / `pause` |
| Report | Report panel | `python3 scripts/ph_cli.py report` |

Each run appends one line to `RUN_REPORT.md`: `MMDDYY HHMM` + summary.

### 4. Smoke / verify

```bash
python3 scripts/operator_api.py --self-check
python3 scripts/ph_cli.py status
python3 scripts/ph_cli.py report
systemctl --user status purple-halo-operator.service   # self checkout
```

See `docs/OPERATOR_RUNBOOK.md` for full operator flow and cross-repo install notes.

## Engineering console (secondary)

Hold mode, goal ledger, budget, diagnostics — debugging only, not daily operation:

```text
http://127.0.0.1:8765/advanced.html
```

## Docs

- Operator path: `docs/OPERATOR_RUNBOOK.md`
- Release notes: `docs/RELEASE_NOTES.md`
- Admin / runtime detail: `docs/ADMIN_HANDOFF.md`
- Mission & success criteria: `project_goals.md`
- Current loop status: `project_status.md`
