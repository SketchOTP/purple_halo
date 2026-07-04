#!/usr/bin/env python3
"""Deterministic verification command selection from changed files. Stdlib only."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

VERIFY = "./scripts/verify.sh"
SYNC_RULES = "./scripts/sync-agent-rules.sh"
CONTEXT_SELF_CHECK = "python3 scripts/select-context.py --self-check"
VERIFY_SELF_CHECK = "python3 scripts/select-verification.py --self-check"
CONTRACTS_SELF_CHECK = "python3 scripts/validate-contracts.py --self-check"
RECORD_SELF_CHECK = "python3 scripts/record-learning-candidate.py --self-check"
COMPACT_SELF_CHECK = "python3 scripts/compact-project-learning.py --self-check"

AGENT_RULE_PATHS = frozenset({"AGENTS.md", ".cursorrules", "CLAUDE.md", "GEMINI.md"})
GOVERNANCE_DOCS = frozenset(
    {
        "README.md",
        "repo_map.md",
        "project_goals.md",
        "project_status.md",
        "project_knowledge.md",
        "docs/verification-harness.md",
        "docs/mimir-tools.md",
        "docs/usage/mcp_cursor.md",
        "docs/orchestration-runtime.md",
    }
)
LEARNING_PATHS = frozenset(
    {
        "scripts/project_learning_lib.py",
        "scripts/record-learning-candidate.py",
        "scripts/compact-project-learning.py",
    }
)
SOURCE_LIKE_SUFFIXES = (".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".toml")


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def normalize_path(path: str) -> str:
    p = Path(path)
    if p.is_absolute():
        try:
            return p.relative_to(ROOT).as_posix()
        except ValueError:
            return path.replace("\\", "/")
    return path.replace("\\", "/")


def merge_changed_file_lists(*lists: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for names in lists:
        for name in names:
            norm = normalize_path(name)
            if norm not in seen:
                seen.add(norm)
                out.append(norm)
    return out


def is_agent_rule(path: str) -> bool:
    return path in AGENT_RULE_PATHS or path.startswith(".cursor/rules/") or path.startswith(".cursor/skills/") or path.startswith(".cursor/commands/")


def is_learning_loop(path: str) -> bool:
    return path in LEARNING_PATHS or path.startswith("project_learning/")


def is_contract_artifact(path: str) -> bool:
    return path.startswith("contracts/") or path == "scripts/validate-contracts.py"


def is_governance_doc(path: str) -> bool:
    return path in GOVERNANCE_DOCS or path.startswith("docs/")


def is_shell_script(path: str) -> bool:
    return path.endswith(".sh")


def is_source_like(path: str) -> bool:
    return any(path.endswith(suffix) for suffix in SOURCE_LIKE_SUFFIXES)


def _git_path_lines(args: list[str]) -> list[str]:
    proc = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def git_changed_files() -> list[str]:
    return merge_changed_file_lists(
        _git_path_lines(["git", "diff", "--name-only"]),
        _git_path_lines(["git", "diff", "--cached", "--name-only"]),
        _git_path_lines(["git", "ls-files", "--others", "--exclude-standard"]),
    )


def select_verification(changed_files: list[str]) -> dict:
    normalized = merge_changed_file_lists(changed_files)
    if not normalized:
        return {
            "changed_files": [],
            "commands": [],
            "reasons": [],
            "warnings": ["No changed files provided or detected."],
        }

    pairs: list[tuple[str, str]] = []
    seen_cmds: set[str] = set()
    warnings: list[str] = []

    def add(command: str, reason: str) -> None:
        if command in seen_cmds:
            return
        seen_cmds.add(command)
        pairs.append((command, reason))

    if any(is_agent_rule(path) for path in normalized):
        add(SYNC_RULES, "Agent contract, Cursor rule, or skill surface changed")
        add(CONTEXT_SELF_CHECK, "Governance routing changed")
        add(VERIFY_SELF_CHECK, "Governance verification routing changed")
        add(VERIFY, "Agent contract, Cursor rule, or skill surface changed")

    if any(is_contract_artifact(path) for path in normalized):
        add(CONTRACTS_SELF_CHECK, "Orchestration contract changed")
        add(VERIFY, "Orchestration contract changed")

    if any(is_learning_loop(path) for path in normalized):
        add(RECORD_SELF_CHECK, "Learning capture changed")
        add(COMPACT_SELF_CHECK, "Learning compactor changed")
        add(VERIFY, "Learning-loop files changed")

    if "scripts/select-context.py" in normalized:
        add(CONTEXT_SELF_CHECK, "scripts/select-context.py changed")
        add(VERIFY, "scripts/select-context.py changed")

    if "scripts/select-verification.py" in normalized:
        add(VERIFY_SELF_CHECK, "scripts/select-verification.py changed")
        add(VERIFY, "scripts/select-verification.py changed")

    shell_scripts = sorted(path for path in normalized if is_shell_script(path))
    for path in shell_scripts:
        add(f"bash -n {path}", f"Shell script changed: {path}")
    if shell_scripts:
        add(VERIFY, "Shell scripts changed")

    if any(is_governance_doc(path) for path in normalized):
        add(VERIFY, "Governance docs changed")

    source_like_fallback: list[str] = []
    for path in normalized:
        if is_agent_rule(path) or is_learning_loop(path) or is_governance_doc(path) or is_shell_script(path):
            continue
        if is_source_like(path):
            source_like_fallback.append(path)
            warnings.append(f"No specific stack verifier exists yet for: {path}")
        else:
            warnings.append(f"No verification route matched: {path}")

    if source_like_fallback:
        add(VERIFY, "Source-like files changed without a stack-specific verifier")

    if normalized and not pairs:
        warnings.append("Changed files were provided, but no verification commands matched. Run ./scripts/verify.sh at minimum or add a route.")

    return {
        "changed_files": normalized,
        "commands": [cmd for cmd, _ in pairs],
        "reasons": [reason for _, reason in pairs],
        "warnings": warnings,
    }


def format_human(result: dict) -> str:
    lines = ["Changed files:"]
    lines.extend(f"- {path}" for path in result["changed_files"] or ["(none)"])
    lines += ["", "Verification plan:"]
    lines.extend(f"- {cmd}" for cmd in result["commands"] or ["(none)"])
    lines += ["", "Reasons:"]
    lines.extend(f"- {reason}" for reason in result["reasons"] or ["(none)"])
    for warning in result["warnings"]:
        lines += ["", f"Warning: {warning}"]
    return "\n".join(lines)


def should_exit_nonzero(result: dict) -> bool:
    if result["changed_files"] and not result["commands"]:
        return True
    if not result["changed_files"] and result["warnings"]:
        return True
    return False


def self_check() -> None:
    merged = merge_changed_file_lists(["a.py", "b.py"], ["b.py", "c.py"], ["d.py"])
    _assert(merged == ["a.py", "b.py", "c.py", "d.py"], "merge must dedupe with stable order")

    result = select_verification(["AGENTS.md"])
    _assert(SYNC_RULES in result["commands"], "AGENTS.md must require sync")
    _assert(CONTEXT_SELF_CHECK in result["commands"], "AGENTS.md must require context self-check")
    _assert(VERIFY in result["commands"], "AGENTS.md must require verify")

    result = select_verification(["scripts/record-learning-candidate.py"])
    _assert(RECORD_SELF_CHECK in result["commands"], "learning script must require record self-check")
    _assert(COMPACT_SELF_CHECK in result["commands"], "learning script must require compact self-check")

    result = select_verification(["scripts/select-context.py"])
    _assert(CONTEXT_SELF_CHECK in result["commands"], "select-context must require self-check")

    result = select_verification(["scripts/mimir_code_nav.py"])
    _assert(VERIFY in result["commands"], "mimir code-nav client must require verify")

    result = select_verification(["contracts/trace.schema.json"])
    _assert(CONTRACTS_SELF_CHECK in result["commands"], "contract change must require contracts self-check")

    result = select_verification(["scripts/audit-agent-rules.sh"])
    _assert("bash -n scripts/audit-agent-rules.sh" in result["commands"], "shell script must require bash -n")
    _assert(VERIFY in result["commands"], "shell script must require verify")

    result = select_verification(["docs/verification-harness.md"])
    _assert(result["commands"] == [VERIFY], "governance doc change must require verify only")

    result = select_verification(["misc/unknown.txt"])
    _assert(any(w.startswith("No verification route matched:") for w in result["warnings"]), "unknown file must warn")
    _assert(not result["commands"], "unknown txt file must not emit commands")
    _assert(should_exit_nonzero(result), "unknown txt file must exit nonzero")

    result = select_verification(["foo/bar.py"])
    _assert(VERIFY in result["commands"], "unknown source file must fall back to verify")
    _assert(any("No specific stack verifier exists yet for:" in w for w in result["warnings"]), "unknown source file must warn")

    result = select_verification([])
    _assert(result["warnings"], "empty input must emit warning")
    _assert(not result["commands"], "empty input must not emit commands")

    result = select_verification(["AGENTS.md", "scripts/select-context.py"])
    _assert(result["commands"].count(VERIFY) == 1, "verify must dedupe")
    _assert(SYNC_RULES in result["commands"], "combo must include sync")

    _assert(isinstance(git_changed_files(), list), "git_changed_files must return a list")
    print("select-verification self-check: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description="Select verification commands for changed files.")
    parser.add_argument("files", nargs="*", help="Changed file paths")
    parser.add_argument("--changed", action="store_true", help="Use git changed files, including untracked")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Emit JSON")
    parser.add_argument("--self-check", action="store_true", help="Run built-in checks")
    args = parser.parse_args()

    if args.self_check:
        try:
            self_check()
        except AssertionError as exc:
            print(f"select-verification self-check: FAIL — {exc}", file=sys.stderr)
            return 1
        return 0

    files = [normalize_path(path) for path in args.files]
    if args.changed:
        files = merge_changed_file_lists(git_changed_files(), files)
    result = select_verification(files)

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        print(format_human(result))
    return 1 if should_exit_nonzero(result) else 0


if __name__ == "__main__":
    raise SystemExit(main())
