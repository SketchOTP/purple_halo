#!/usr/bin/env python3
"""OpenAI-compatible Paragon client used by the implementation worker."""
from __future__ import annotations
import json, os, ssl, urllib.request
from typing import Any

PARAGON_BASE_URL = os.environ.get("PARAGON_BASE_URL", "https://atlas-2.tail1a5964.ts.net:10000/v1").rstrip("/")
PARAGON_MODEL = os.environ.get("PARAGON_MODEL", "paragon")
PARAGON_API_KEY = os.environ.get("PARAGON_API_KEY", "routerbot")

def is_configured() -> bool:
    return bool(PARAGON_BASE_URL and PARAGON_MODEL and PARAGON_API_KEY)

def generate_execution_steps(contract: dict[str, Any]) -> list[dict[str, Any]]:
    request = {k: contract.get(k) for k in ("objective", "constraints", "target_files", "expected_outputs", "verification_commands")}
    body = json.dumps({"model": PARAGON_MODEL, "temperature": 0, "messages": [
        {"role": "system", "content": "Return only a JSON array of bounded execution steps. Use write_file only for target_files; paths must be relative."},
        {"role": "user", "content": json.dumps(request)},
    ]}).encode()
    req = urllib.request.Request(PARAGON_BASE_URL + "/chat/completions", data=body, headers={"Authorization": f"Bearer {PARAGON_API_KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120, context=ssl.create_default_context()) as response:
        payload = json.loads(response.read().decode())
    steps = json.loads(payload["choices"][0]["message"]["content"])
    if not isinstance(steps, list):
        raise ValueError("Paragon returned non-list execution steps")
    allowed = set(contract.get("target_files") or [])
    for step in steps:
        if step.get("type") == "write_file":
            path = str(step.get("path") or "")
            if path.startswith("/") or ".." in path.split("/") or path not in allowed:
                raise ValueError(f"Paragon proposed non-target write: {path}")
    return steps

if __name__ == "__main__":
    print(json.dumps({"configured": is_configured(), "base_url": PARAGON_BASE_URL, "model": PARAGON_MODEL}))
