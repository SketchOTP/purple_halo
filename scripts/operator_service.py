#!/usr/bin/env python3
"""Persistent purple_halo operator service for Atlas (API/UI + schedule ticker)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import traceback
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from operator_runtime import (  # noqa: E402
    read_service_status,
    service_unit_for_repo,
    startup_health_checks,
    write_service_status,
)


def _run_due_tick() -> None:
    try:
        subprocess.run(
            [sys.executable, str(SCRIPTS / "loop_schedule.py"), "--run-due"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=180,
            env={**dict(__import__("os").environ), "PYTHONPATH": str(SCRIPTS), "MIMIR_ENDPOINT": ""},
        )
    except Exception as exc:
        write_service_status(last_schedule_error=str(exc)[:300])


def _ticker(stop: threading.Event, interval_sec: int = 30) -> None:
    # Align first tick slightly after start to avoid boot stampede.
    stop.wait(5)
    while not stop.is_set():
        _run_due_tick()
        stop.wait(interval_sec)


def main() -> int:
    parser = argparse.ArgumentParser(description="purple_halo persistent operator service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-ticker", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()

    if args.self_check:
        health = startup_health_checks()
        assert health.get("ok") is True
        print("operator-service: PASS")
        return 0

    write_service_status(state="starting", last_failure="", pid=__import__("os").getpid(), unit=service_unit_for_repo(ROOT))
    try:
        health = startup_health_checks()
        write_service_status(health=health)
        if not health.get("ok"):
            write_service_status(
                state="failed",
                last_failure="startup health checks failed: " + json.dumps(health)[:500],
            )
            return 1

        # Import API handler after health so corrupt state is repaired first.
        import operator_api as api

        stop = threading.Event()
        ticker_thread = None
        if not args.no_ticker:
            ticker_thread = threading.Thread(target=_ticker, args=(stop,), daemon=True)
            ticker_thread.start()

        server = ThreadingHTTPServer((args.host, args.port), api.Handler)
        write_service_status(
            state="up",
            last_start=health.get("checked_at"),
            last_failure="",
            listen=f"http://{args.host}:{args.port}/",
            unit=service_unit_for_repo(ROOT),
            health=health,
        )
        print(f"purple_halo service up: http://{args.host}:{args.port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            server.server_close()
            write_service_status(state="down")
        return 0
    except Exception as exc:
        write_service_status(state="failed", last_failure=str(exc)[:500], traceback=traceback.format_exc()[-1500:])
        raise


if __name__ == "__main__":
    raise SystemExit(main())