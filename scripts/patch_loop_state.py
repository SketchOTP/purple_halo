#!/usr/bin/env python3
from pathlib import Path
import hashlib, shutil
p = Path("scripts/loop_state.py")
source = Path("scripts/loop_state_fragment.py")
expected = __import__("os").environ.get("PURPLE_HALO_EXPECTED_LOOP_STATE_SHA256")
if expected and hashlib.sha256(p.read_bytes()).hexdigest() != expected:
    raise SystemExit("refusing blind patch: loop_state.py hash does not match expected")
shutil.copy2(p, p.with_suffix(p.suffix + ".bak"))
p.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
print("patched")
