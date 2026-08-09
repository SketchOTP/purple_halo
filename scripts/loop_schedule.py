#!/usr/bin/env python3
"""Schedule-driven loop runner for purple_halo. Stdlib only."""

from __future__ import annotations

import argparse
import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "project_memory" / "runtime" / "schedule.default.json"
ACTIVE = ROOT / "project_memory" / "runtime" / "schedule.json"
HISTORY_PATH = ROOT / "project_memory" / "runtime" / "schedule_run_history.json"
LOOP = ROOT / "scripts" / "purple_halo_loop.py"

_DAY_NAMES = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_FRESH_REPO_SCHEDULE: dict[str, Any] = {
    "enabled": False,
    "timezone": "UTC",
    "schedule_kind": "interval",
    "every_hours": 2,
    "for_days": None,
    "until_goal_achieved": False,
    "runs": [],
    "max_runs_per_day": 24,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_schedule() -> dict[str, Any]:
    if not ACTIVE.is_file() and not DEFAULT.is_file():
        return dict(_FRESH_REPO_SCHEDULE)
    path = ACTIVE if ACTIVE.is_file() else DEFAULT
    return json.loads(path.read_text(encoding="utf-8"))


def save_schedule(schedule: dict[str, Any]) -> None:
    ACTIVE.parent.mkdir(parents=True, exist_ok=True)
    tmp = ACTIVE.with_suffix(ACTIVE.suffix + ".tmp")
    tmp.write_text(json.dumps(schedule, indent=2) + "\n", encoding="utf-8")
    tmp.replace(ACTIVE)


def _parse_iso(ts: str) -> datetime | None:
    text = str(ts or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def load_run_history() -> dict[str, Any]:
    if not HISTORY_PATH.is_file():
        return {"attempts": [], "last_failure": None, "retry_count": 0}
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # Do not invent a blank history — a later save would wipe durable flags.
        return {"attempts": [], "last_failure": None, "retry_count": 0, "_load_failed": True}
    if not isinstance(data, dict):
        return {"attempts": [], "last_failure": None, "retry_count": 0, "_load_failed": True}
    data.setdefault("attempts", [])
    data.setdefault("last_failure", None)
    data.setdefault("retry_count", 0)
    return data


def save_run_history(history: dict[str, Any]) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if history.pop("_load_failed", False):
        # Refuse to overwrite a history file we failed to read.
        return
    # Preserve durable flags other writers may have set since we loaded.
    try:
        if HISTORY_PATH.is_file():
            on_disk = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            if isinstance(on_disk, dict):
                for key, value in on_disk.items():
                    if key not in history:
                        history[key] = value
    except (OSError, json.JSONDecodeError):
        pass
    tmp = HISTORY_PATH.with_suffix(HISTORY_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")
    tmp.replace(HISTORY_PATH)


def append_run_record(
    *,
    trigger: str,
    status: str,
    cycle_id: int | None = None,
    error: str = "",
    started_at: str | None = None,
    finished_at: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    history = load_run_history()
    record = {
        "id": str(uuid.uuid4())[:8],
        "started_at": started_at or _now_iso(),
        "finished_at": finished_at or _now_iso(),
        "trigger": trigger,
        "status": status,
        "cycle_id": cycle_id,
        "error": error,
        "retry_count": int(history.get("retry_count") or 0),
    }
    if extra:
        record.update(extra)
    history.setdefault("attempts", []).append(record)
    history["attempts"] = history["attempts"][-500:]
    if status == "failure":
        history["retry_count"] = int(history.get("retry_count") or 0) + 1
        history["last_failure"] = record
    elif status == "success":
        history["retry_count"] = 0
        history["last_failure"] = None
    save_run_history(history)
    return record


def _current_hhmm() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M")


def _schedule_now(schedule: dict[str, Any]) -> datetime:
    tz_name = str(schedule.get("timezone") or "UTC")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
    return datetime.now(tz)


def _parse_slot_days(days: Any) -> set[int] | None:
    """None = all days; otherwise weekday ints 0=Mon .. 6=Sun."""
    if days is None or days == [] or days == "*" or days == "all":
        return None
    if isinstance(days, str):
        days = [days]
    out: set[int] = set()
    for raw in days:
        if isinstance(raw, int) and 0 <= raw <= 6:
            out.add(raw)
            continue
        key = str(raw).strip().lower()[:3]
        if key in _DAY_NAMES:
            out.add(_DAY_NAMES[key])
        elif str(raw).strip().lower() in ("*", "all"):
            return None
    return out or None


def _week_interval_ok(schedule: dict[str, Any], now: datetime) -> bool:
    every = int(schedule.get("every_weeks") or 1)
    if every <= 1:
        return True
    anchor_raw = schedule.get("campaign_started_at") or schedule.get("schedule_anchor")
    if not anchor_raw:
        return True
    anchor = _parse_iso(str(anchor_raw))
    if anchor is None:
        return True
    if now.tzinfo is not None:
        anchor = anchor.astimezone(now.tzinfo)
    weeks = (now.date() - anchor.date()).days // 7
    return weeks >= 0 and weeks % every == 0


def _times_slots_due(schedule: dict[str, Any]) -> list[dict[str, Any]]:
    now = _schedule_now(schedule)
    if not _week_interval_ok(schedule, now):
        return []
    hhmm = now.strftime("%H:%M")
    wd = now.weekday()
    due: list[dict[str, Any]] = []
    for slot in schedule.get("runs") or []:
        if str(slot.get("at") or "") != hhmm:
            continue
        allowed = _parse_slot_days(slot.get("days"))
        if allowed is not None and wd not in allowed:
            continue
        due.append(slot)
    return due


def campaign_stop_reason(schedule: dict[str, Any] | None = None) -> str:
    """Return stop reason if campaign should end (goal or days), else empty."""
    schedule = schedule or load_schedule()
    if schedule.get("until_goal_achieved"):
        try:
            from loop_goal_delivery import criteria_complete

            if criteria_complete():
                return "goal_achieved"
        except Exception:
            pass
    for_days = schedule.get("for_days")
    started = schedule.get("campaign_started_at")
    if for_days is not None and started:
        start_dt = _parse_iso(str(started))
        if start_dt is not None:
            elapsed_days = (datetime.now(timezone.utc) - start_dt).total_seconds() / 86400.0
            if elapsed_days >= float(for_days):
                return "campaign_days_elapsed"
    return ""


def maybe_stop_campaign() -> dict[str, Any] | None:
    schedule = load_schedule()
    reason = campaign_stop_reason(schedule)
    if not reason:
        return None
    if schedule.get("campaign_stop_reason") == reason and not schedule.get("enabled"):
        return {"stopped": True, "reason": reason, "already": True}
    schedule["enabled"] = False
    schedule["campaign_stop_reason"] = reason
    schedule["campaign_stopped_at"] = _now_iso()
    save_schedule(schedule)
    try:
        from run_report import append_line

        label = "Goal achieved" if reason == "goal_achieved" else "Campaign days elapsed"
        append_line(f"Stopped: {label}")
    except Exception:
        pass
    return {"stopped": True, "reason": reason, "already": False}


def _interval_slot_key(every_hours: float) -> str:
    period = max(int(float(every_hours) * 3600), 60)
    bucket = int(datetime.now(timezone.utc).timestamp()) // period
    return f"interval-{bucket}"


def _interval_slots_due(schedule: dict[str, Any]) -> list[dict[str, Any]]:
    every_h = float(schedule.get("every_hours") or 0)
    if every_h <= 0:
        return []
    history = load_run_history()
    last_ts = None
    for rec in reversed(list(history.get("attempts") or [])):
        if rec.get("status") in ("success", "failure"):
            last_ts = rec.get("finished_at") or rec.get("started_at")
            break
    key = _interval_slot_key(every_h)
    slot = {"at": key, "label": "interval"}
    if last_ts is None:
        return [slot]
    last_dt = _parse_iso(str(last_ts))
    if last_dt is None:
        return [slot]
    elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
    if elapsed >= every_h * 3600:
        return [slot]
    return []


def slots_due_now(schedule: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    schedule = schedule or load_schedule()
    if not schedule.get("enabled"):
        return []
    if campaign_stop_reason(schedule):
        return []
    kind = str(schedule.get("schedule_kind") or "")
    if kind == "interval" or schedule.get("every_hours"):
        return _interval_slots_due(schedule)
    return _times_slots_due(schedule)


def run_loop(*, trigger: str, decision: dict[str, Any] | None = None) -> dict[str, Any]:
    from loop_autonomous import build_run_decision, record_autonomous_run

    started = _now_iso()
    decision = decision or {"trigger": trigger, "decided_at": started, "allow": True, "classification": "run"}
    proc = subprocess.run(["python3", str(LOOP), "run"], cwd=ROOT, capture_output=True, text=True)
    cycle_id = None
    status = "failure"
    error = ""
    payload: dict[str, Any] = {}
    try:
        payload = json.loads(proc.stdout)
        cycle_id = payload.get("cycle_id")
        if proc.returncode == 0 and (
            payload.get("meaningful_product_progress")
            or payload.get("verification_passed")
            or payload.get("blocked_classification")
        ):
            # honest blocked classification is a successful autonomous control outcome
            if payload.get("meaningful_product_progress") or payload.get("blocked_classification"):
                status = "success"
            elif payload.get("verification_passed"):
                status = "success"
            else:
                error = (proc.stderr or proc.stdout or "verification failed")[:500]
        else:
            error = (proc.stderr or proc.stdout or "verification failed")[:500]
    except json.JSONDecodeError:
        error = (proc.stderr or proc.stdout or "invalid loop output")[:500]
    run_decision = payload.get("run_decision") or build_run_decision(decision=decision, cycle_result=payload)
    sequence_entry = payload.get("sequence_entry") or {}
    record = append_run_record(
        trigger=trigger,
        status=status,
        cycle_id=cycle_id,
        error=error,
        started_at=started,
        finished_at=_now_iso(),
        extra={
            "outcome_class": sequence_entry.get("outcome_class"),
            "meaningful_product_progress": sequence_entry.get("meaningful_product_progress"),
            "blocked_classification": sequence_entry.get("blocked_classification"),
            "why_run": sequence_entry.get("why_run"),
            "why_selected": sequence_entry.get("why_selected"),
            "decision_id": decision.get("decision_id"),
        },
    )
    report_line = ""
    try:
        from run_report import append_line, summarize_run

        summary_payload = dict(payload)
        if sequence_entry.get("why_selected") and not summary_payload.get("why_selected"):
            summary_payload["why_selected"] = sequence_entry.get("why_selected")
        if sequence_entry.get("why_run") and not summary_payload.get("why_run"):
            summary_payload["why_run"] = sequence_entry.get("why_run")
        report_line = append_line(
            summarize_run(status=status, error=error, payload=summary_payload)
        )
    except Exception:
        pass
    # Stop campaign after a run if goal/days threshold is met.
    maybe_stop_campaign()
    return {
        "ran": True,
        "record": record,
        "sequence_entry": sequence_entry,
        "run_decision": run_decision,
        "exit_code": proc.returncode,
        "loop_stdout": proc.stdout.strip(),
        "cycle_result": payload,
        "report_line": report_line,
    }


def _scheduler_gate(trigger: str) -> dict[str, Any] | None:
    from loop_autonomous import decide_autonomous_run, record_autonomous_run
    from loop_state import load_state

    decision = decide_autonomous_run(trigger=trigger, state=load_state())
    if decision.get("allow"):
        return {"decision": decision}
    classification = str(decision.get("classification") or "no_meaningful_product_step")
    sequence_entry = record_autonomous_run(decision=decision, cycle_result={}, ran=False)
    record = append_run_record(
        trigger=trigger,
        status="skipped",
        error=classification,
        extra={
            "outcome_class": sequence_entry.get("outcome_class"),
            "why_run": decision.get("why_run"),
            "decision_id": decision.get("decision_id"),
        },
    )
    return {
        "ran": False,
        "reason": classification,
        "record": record,
        "sequence_entry": sequence_entry,
        "run_decision": decision,
    }


def run_due() -> dict[str, Any]:
    stopped = maybe_stop_campaign()
    if stopped and not stopped.get("already"):
        return {"ran": False, "reason": stopped.get("reason"), "campaign_stopped": True}
    if stopped and stopped.get("already"):
        return {"ran": False, "reason": stopped.get("reason"), "campaign_stopped": True}
    due = slots_due_now()
    if not due:
        return {"ran": False, "reason": "no_due_slot"}
    gate = _scheduler_gate("scheduled")
    if gate and not gate.get("decision", {}).get("allow", True) and gate.get("ran") is False:
        return gate
    decision = (gate or {}).get("decision")
    # Restart-safe: claim slot before execution so crash/restart cannot double-fire.
    if due:
        try:
            from operator_runtime import claim_schedule_slot
            claimed_any = False
            for slot in due:
                claim = claim_schedule_slot(str(slot.get("at") or ""))
                if claim.get("claimed"):
                    claimed_any = True
                    break
            if not claimed_any:
                from loop_autonomous import record_autonomous_run
                decision = decision or {
                    "trigger": "scheduled",
                    "allow": False,
                    "classification": "slot_already_claimed",
                    "why_run": "schedule slot already claimed for today (restart-safe)",
                    "continue_later": True,
                    "continue_reason": "wait for next schedule window",
                    "stop_condition": "slot_already_claimed",
                }
                decision["allow"] = False
                decision["classification"] = "slot_already_claimed"
                sequence_entry = record_autonomous_run(decision=decision, cycle_result={}, ran=False)
                record = append_run_record(
                    trigger="scheduled",
                    status="skipped",
                    error="slot_already_claimed",
                    extra={"outcome_class": "slot_already_claimed", "decision_id": decision.get("decision_id")},
                )
                return {
                    "ran": False,
                    "reason": "slot_already_claimed",
                    "record": record,
                    "sequence_entry": sequence_entry,
                    "run_decision": decision,
                }
        except Exception:
            pass
    if not due:
        decision = decision or {
            "trigger": "scheduled",
            "decided_at": _now_iso(),
            "allow": False,
            "classification": "no_due_slot",
            "why_run": "no schedule window is due now",
            "continue_later": True,
            "continue_reason": "wait for next configured schedule window",
            "stop_condition": "no_due_slot",
            "decision_id": str(uuid.uuid4())[:8],
        }
        decision["classification"] = "no_due_slot"
        decision["allow"] = False
        decision["why_run"] = "no schedule window is due now"
        return {
            "ran": False,
            "reason": "no_due_slot",
            "run_decision": decision,
        }
    return run_loop(trigger="scheduled", decision=decision)


def run_now() -> dict[str, Any]:
    gate = _scheduler_gate("manual")
    if gate and gate.get("ran") is False:
        return gate
    return run_loop(trigger="manual", decision=(gate or {}).get("decision"))


def self_check() -> None:
    from loop_autonomous import decide_autonomous_run, evaluate_product_complete
    from run_report import summarize_run

    schedule = load_schedule()
    assert "runs" in schedule or schedule.get("every_hours") or schedule.get("schedule_kind") == "interval"
    hist = load_run_history()
    assert isinstance(hist.get("attempts"), list)
    decision = decide_autonomous_run(trigger="manual")
    assert "allow" in decision
    complete = evaluate_product_complete()
    assert "product_complete" in complete
    assert _interval_slot_key(2.0).startswith("interval-")
    assert _parse_slot_days(["mon", "wed"]) == {0, 2}
    assert _parse_slot_days(None) is None
    assert summarize_run(status="failure", error="boom").startswith("Failed:")
    due = _interval_slots_due({"every_hours": 2})
    assert due and str(due[0].get("at") or "").startswith("interval-")
    print("loop-schedule: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description="purple_halo schedule runner")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--run-due", action="store_true")
    parser.add_argument("--run-now", action="store_true")
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    if args.show:
        print(json.dumps(load_schedule(), indent=2))
        return 0
    if args.history:
        print(json.dumps(load_run_history(), indent=2))
        return 0
    if args.run_due:
        result = run_due()
        print(json.dumps(result, indent=2))
        return 0 if result.get("ran") or result.get("reason") == "no_due_slot" else 1
    if args.run_now:
        result = run_now()
        print(json.dumps(result, indent=2))
        return 0 if result["record"]["status"] == "success" else 1
    parser.error("specify --self-check, --show, --run-due, --run-now, or --history")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
