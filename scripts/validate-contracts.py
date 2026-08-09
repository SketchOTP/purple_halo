#!/usr/bin/env python3
"""Validate checked-in JSON contracts and live runtime envelopes."""
from __future__ import annotations
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

def self_check() -> None:
    schemas = list((ROOT / "contracts").glob("*.schema.json"))
    assert schemas, "no contract schemas"
    for path in schemas:
        data = load(path)
        assert data.get("type") == "object", path
    for path in (ROOT / "project_memory/runtime").glob("*.json"):
        if path.name.startswith("_") or ".tmp" in path.name or ".corrupt." in path.name:
            continue
        try: load(path)
        except json.JSONDecodeError as exc: raise AssertionError(f"invalid runtime JSON: {path}: {exc}")
    print(f"validate-contracts: PASS ({len(schemas)} schemas)")

if __name__ == "__main__":
    if "--self-check" not in sys.argv: raise SystemExit("specify --self-check")
    self_check()
