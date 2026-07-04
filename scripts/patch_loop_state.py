#!/usr/bin/env python3
from pathlib import Path
p = Path("scripts/loop_state.py")
p.write_text(Path("scripts/loop_state_fragment.py").read_text(encoding="utf-8"), encoding="utf-8")
print("patched")
