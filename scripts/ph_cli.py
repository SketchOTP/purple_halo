#!/usr/bin/env python3
"""Simple purple_halo operator: install, frequency, play, report. Stdlib only."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
RUNTIME = ROOT / "project_memory" / "runtime"
SCHEDULE_PATH = RUNTIME / "schedule.json"
HISTORY_PATH = RUNTIME / "schedule_run_history.json"
REPORT_PATH = ROOT / "RUN_REPORT.md"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.is_file():
        return dict(default or {})
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(default or {})
    return data if isinstance(data, dict) else dict(default or {})


def _save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def parse_every(text: str) -> float:
    """Parse '2h', '2H', '120m', '1d' into hours."""
    raw = str(text or "").strip().lower().replace(" ", "")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(h|hr|hrs|hour|hours|m|min|mins|d|day|days)?", raw)
    if not m:
        raise SystemExit(f"cannot parse interval: {text!r} (try 2h, 30m, 1d)")
    value = float(m.group(1))
    if value <= 0:
        raise SystemExit("interval must be greater than zero")
    unit = m.group(2) or "h"
    if unit.startswith("m"):
        return value / 60.0
    if unit.startswith("d"):
        return value * 24.0
    return value


def set_schedule_config(
    *,
    kind: str,
    every: str | None = None,
    for_days: float | None = None,
    until_goal: bool = True,
    runs: list[dict[str, Any]] | None = None,
    every_weeks: int = 1,
    timezone: str | None = None,
) -> dict[str, Any]:
    schedule = _load_json(SCHEDULE_PATH, default={"enabled": False, "runs": []})
    kind = str(kind or "interval").strip().lower()
    if kind not in ("interval", "times"):
        raise SystemExit(f"unknown schedule kind: {kind!r}")
    schedule["schedule_kind"] = kind
    schedule["until_goal_achieved"] = bool(until_goal)
    schedule["timezone"] = str(timezone or schedule.get("timezone") or "UTC")
    schedule["every_weeks"] = max(1, int(every_weeks or 1))
    if kind == "interval":
        if not every:
            raise SystemExit("interval schedule requires every (e.g. 2h)")
        schedule["every_hours"] = parse_every(every)
        schedule["for_days"] = float(for_days) if for_days is not None else None
    else:
        schedule.pop("every_hours", None)
        if for_days is not None:
            schedule["for_days"] = float(for_days)
        cleaned: list[dict[str, Any]] = []
        for slot in runs or []:
            at = str(slot.get("at") or "").strip()
            if not at:
                continue
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", at):
                raise SystemExit(f"invalid run time: {at!r} (use HH:MM)")
            entry: dict[str, Any] = {"at": at}
            days = slot.get("days")
            if days:
                entry["days"] = days
            label = str(slot.get("label") or "").strip()
            if label:
                entry["label"] = label
            cleaned.append(entry)
        if not cleaned:
            raise SystemExit("times schedule requires at least one run time")
        schedule["runs"] = cleaned
    _save_json(SCHEDULE_PATH, schedule)
    return schedule


def set_frequency(
    *,
    every: str,
    for_days: float | None,
    until_goal: bool,
) -> dict[str, Any]:
    return set_schedule_config(
        kind="interval",
        every=every,
        for_days=for_days,
        until_goal=until_goal,
    )


def play() -> dict[str, Any]:
    schedule = _load_json(SCHEDULE_PATH, default={"runs": []})
    schedule["enabled"] = True
    if not schedule.get("campaign_started_at"):
        schedule["campaign_started_at"] = _now_iso()
    schedule.pop("campaign_stopped_at", None)
    schedule.pop("campaign_stop_reason", None)
    _save_json(SCHEDULE_PATH, schedule)

    history = _load_json(
        HISTORY_PATH,
        default={"attempts": [], "sequence": [], "autonomous_allowed": True},
    )
    history["autonomous_allowed"] = True
    history["stop_classification"] = ""
    history["stop_reason"] = ""
    # Project mode: actively build toward the goal (not production freeze).
    history["production_hold_mode"] = False
    history["feature_freeze"] = False
    history["architecture_freeze"] = False
    _save_json(HISTORY_PATH, history)

    if not REPORT_PATH.is_file():
        REPORT_PATH.write_text("# purple_halo run report\n\n", encoding="utf-8")
    from run_report import append_line

    append_line("Play: auto-run started")
    return {"schedule": schedule, "playing": True}


def pause() -> dict[str, Any]:
    schedule = _load_json(SCHEDULE_PATH, default={"runs": []})
    schedule["enabled"] = False
    _save_json(SCHEDULE_PATH, schedule)
    history = _load_json(HISTORY_PATH, default={"attempts": [], "sequence": []})
    history["autonomous_allowed"] = False
    history["stop_classification"] = "operator_pause"
    history["stop_reason"] = "operator paused"
    _save_json(HISTORY_PATH, history)
    from run_report import append_line

    append_line("Pause: auto-run stopped")
    return {"schedule": schedule, "playing": False}


def set_goal(goal_path: str) -> Path:
    src = Path(goal_path).expanduser().resolve()
    if not src.is_file():
        raise SystemExit(f"goal file not found: {src}")
    return set_goal_content(src.read_text(encoding="utf-8"))


def set_goal_content(content: str) -> Path:
    dest = ROOT / "project_goals.md"
    text = (content or "").rstrip()
    dest.write_text(text + ("\n" if text else ""), encoding="utf-8")
    schedule = _load_json(SCHEDULE_PATH, default={"runs": []})
    schedule["goal_file"] = "project_goals.md"
    schedule.pop("goal_file_source", None)
    _save_json(SCHEDULE_PATH, schedule)
    return dest


def status_text() -> str:
    schedule = _load_json(SCHEDULE_PATH)
    history = _load_json(HISTORY_PATH)
    playing = bool(schedule.get("enabled")) and bool(history.get("autonomous_allowed", True))
    every = schedule.get("every_hours")
    for_days = schedule.get("for_days")
    until_goal = schedule.get("until_goal_achieved")
    goal = ROOT / "project_goals.md"
    lines = [
        f"repo: {ROOT}",
        f"playing: {'yes' if playing else 'no'}",
        f"goal: {goal if goal.is_file() else '(missing project_goals.md)'}",
        f"every: {every}h" if every else "every: (not set)",
        f"for_days: {for_days}" if for_days is not None else "for_days: (none)",
        f"until_goal: {'yes' if until_goal else 'no'}",
        f"campaign_started_at: {schedule.get('campaign_started_at') or '(not started)'}",
        f"report: {REPORT_PATH}",
    ]
    if schedule.get("campaign_stop_reason"):
        lines.append(f"stopped: {schedule.get('campaign_stop_reason')}")
    attempts = history.get("attempts") or []
    if attempts:
        last = attempts[-1]
        lines.append(
            f"last_run: {last.get('finished_at') or last.get('started_at')} "
            f"{last.get('status')} {last.get('error') or ''}".rstrip()
        )
    return "\n".join(lines)


def show_report() -> str:
    if not REPORT_PATH.is_file():
        return "(no report yet)"
    return REPORT_PATH.read_text(encoding="utf-8")


def cmd_install(args: argparse.Namespace) -> int:
    installer = SCRIPTS / "install_to_repo.sh"
    if not installer.is_file():
        raise SystemExit(f"missing installer: {installer}")
    cmd = ["bash", str(installer), str(Path(args.repo).expanduser().resolve())]
    if args.goal:
        cmd.extend(["--goal", str(Path(args.goal).expanduser().resolve())])
    if args.no_service:
        cmd.append("--no-service")
    proc = subprocess.run(cmd, cwd=ROOT)
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ph",
        description="purple_halo simple operator: install, frequency, play, report",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_install = sub.add_parser("install", help="install purple_halo into a repo")
    p_install.add_argument("repo", help="destination repo path")
    p_install.add_argument("--goal", help="goal file to copy into the repo as project_goals.md")
    p_install.add_argument("--no-service", action="store_true", help="skip systemd user service")

    p_freq = sub.add_parser("frequency", help="set run frequency")
    p_freq.add_argument("--every", required=True, help="interval, e.g. 2h, 30m, 1d")
    p_freq.add_argument("--for-days", type=float, default=None, help="stop after N days")
    p_freq.add_argument(
        "--until-goal",
        action="store_true",
        help="stop when goal success criteria are complete",
    )

    sub.add_parser("play", help="start auto-run")
    sub.add_parser("pause", help="stop auto-run")
    sub.add_parser("status", help="show simple status")
    sub.add_parser("report", help="print RUN_REPORT.md")

    p_goal = sub.add_parser("goal", help="point at / copy a goal file")
    p_goal.add_argument("path", help="path to goal file")

    p_run = sub.add_parser("run-now", help="run one cycle now")

    args = parser.parse_args(argv)
    sys.path.insert(0, str(SCRIPTS))

    if args.cmd == "install":
        return cmd_install(args)
    if args.cmd == "frequency":
        schedule = set_frequency(
            every=args.every,
            for_days=args.for_days,
            until_goal=args.until_goal,
        )
        bits = [f"every {schedule['every_hours']}h"]
        if schedule.get("for_days") is not None:
            bits.append(f"for {schedule['for_days']} days")
        if schedule.get("until_goal_achieved"):
            bits.append("or until goal achieved")
        print("frequency set:", ", ".join(bits), "(whichever comes first)" if len(bits) > 1 else "")
        return 0
    if args.cmd == "play":
        play()
        print("playing")
        print(status_text())
        return 0
    if args.cmd == "pause":
        pause()
        print("paused")
        return 0
    if args.cmd == "status":
        print(status_text())
        return 0
    if args.cmd == "report":
        print(show_report())
        return 0
    if args.cmd == "goal":
        dest = set_goal(args.path)
        print(f"goal set: {dest}")
        return 0
    if args.cmd == "run-now":
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "loop_schedule.py"), "--run-now"],
            cwd=ROOT,
            env={**dict(__import__("os").environ), "PYTHONPATH": str(SCRIPTS), "MIMIR_ENDPOINT": ""},
        )
        return proc.returncode
    parser.error(f"unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
