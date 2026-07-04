#!/usr/bin/env python3
"""Deterministic task routing and context selection. Stdlib only."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from mimir_code_nav import mimir_available, navigate_for_task

ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = ROOT / "project_memory" / "index.json"

REQUIRED = [
    "AGENTS.md",
    "project_goals.md",
    "project_status.md",
    "repo_map.md",
]

OPTIONAL_HOT = ["project_learning/active.md"]

TASK_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("analysis", ("explain", "analyze", "analysis", "review", "understand", "what is", "why is")),
    ("trivial_edit", ("typo", "rename", "comment", "docs-only", "readme", "wording", "one line")),
    ("governance", ("agent", "governance", "contract", "rules", "mcp", "memory", "drift", "token", "prompt")),
    ("risky_change", ("architecture", "schema", "api", "migration", "release", "cross-cutting", "multi-file")),
    ("bounded_implementation", ("build", "implement", "add", "fix", "change", "refactor", "improve")),
]

# ponytail: keyword routing is enough for the current governance kernel; upgrade to a scored classifier if route errors recur.
ROUTES: dict[str, dict[str, object]] = {
    "analysis": {
        "route": "direct-analysis",
        "meaningful": True,
        "preflight": ["mimir", "serena"],
        "mcp_actions": [
            "memory_recall",
            "Use Mimir code navigation and Serena before broad repo reads",
        ],
        "recommended": [
            "docs/verification-harness.md",
            "docs/mimir-tools.md",
            "docs/orchestration-runtime.md",
            "project_learning/active.md",
        ],
    },
    "trivial_edit": {
        "route": "direct-trivial",
        "meaningful": False,
        "preflight": [],
        "mcp_actions": ["Direct work allowed; still inspect the target and verify if a quick check exists"],
        "recommended": [],
    },
    "governance": {
        "route": "direct-governance",
        "meaningful": True,
        "preflight": ["mimir", "serena"],
        "mcp_actions": [
            "memory_recall",
            "memory_search before replacing an existing workflow",
            "Use Mimir code navigation and Serena before broad repo reads",
            "Use agent governance gates before risky direct changes",
            "Load only the MCPs required for the task",
        ],
        "recommended": [
            "README.md",
            "docs/orchestration-runtime.md",
            "docs/verification-harness.md",
            "docs/mimir-tools.md",
            "contracts/trace.schema.json",
            "contracts/eval-result.schema.json",
            "contracts/memory-fact.schema.json",
            "contracts/hook-event.schema.json",
            "contracts/session-resume.schema.json",
            "scripts/verify.sh",
            "scripts/select-verification.py",
            "scripts/validate-contracts.py",
            "project_learning/active.md",
            "project_knowledge.md",
        ],
    },
    "bounded_implementation": {
        "route": "direct-implementation",
        "meaningful": True,
        "preflight": ["mimir", "serena"],
        "mcp_actions": [
            "memory_recall",
            "memory_search before modifying existing functionality",
            "Use Mimir code navigation and Serena before broad repo reads",
            "Load only the MCPs required for the task",
        ],
        "recommended": [
            "docs/orchestration-runtime.md",
            "docs/verification-harness.md",
            "docs/mimir-tools.md",
            "contracts/trace.schema.json",
            "contracts/eval-result.schema.json",
            "project_learning/active.md",
        ],
    },
    "risky_change": {
        "route": "governed-direct",
        "meaningful": True,
        "preflight": ["mimir", "serena"],
        "mcp_actions": [
            "memory_recall",
            "memory_search before modifying existing functionality",
            "Use Mimir code navigation and Serena before broad repo reads",
            "Follow agent approval gates and cursor_session.py for risky direct implementation",
            "Load only the MCPs required for the task",
        ],
        "recommended": [
            ".cursor/rules/03-approval-gates.mdc",
            "docs/usage/mcp_cursor.md",
            "docs/orchestration-runtime.md",
            "docs/verification-harness.md",
            "contracts/trace.schema.json",
            "contracts/eval-result.schema.json",
            "contracts/session-resume.schema.json",
            ".architect/alignment_report.md",
            ".architect/repository_truth.json",
            "project_learning/active.md",
        ],
    },
}

INDEX_HINTS: dict[str, tuple[str, ...]] = {
    "verification": ("verify", "test", "validation", "audit"),
    "governance": ("governance", "rsal", "alignment", "drift", "approval"),
    "mimir": ("mimir", "memory", "outcome", "recall"),
    "contracts": ("trace", "hook", "eval", "resume"),
}


def file_chars(rel: str) -> int:
    path = ROOT / rel
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    if path.is_dir():
        return sum(file_chars(p.relative_to(ROOT).as_posix()) for p in path.rglob("*") if p.is_file())
    return 0


def estimate_tokens(rel: str) -> int:
    return file_chars(rel) // 4


def task_lower(task: str) -> str:
    return task.casefold().strip()


def classify_task(task: str) -> str:
    text = task_lower(task)
    for kind, keywords in TASK_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return kind
    return "bounded_implementation"


def index_hits(task: str) -> list[str]:
    if not INDEX_PATH.is_file():
        return []
    try:
        payload = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    text = task_lower(task)
    hits: list[str] = []
    for entry in payload.get("files", []):
        path = entry.get("path", "")
        summary = entry.get("summary", "")
        tokens = re.findall(r"[a-z0-9]+", f"{path} {summary}".casefold())
        if any(token in text for token in tokens if len(token) >= 5):
            hits.append(path)
    return hits


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _should_use_code_navigation(task: str, kind: str) -> bool:
    text = task_lower(task)
    if kind in {"trivial_edit", "analysis"}:
        return False
    return any(
        token in text
        for token in (
            "implement",
            "refactor",
            "fix",
            "change",
            "schema",
            "api",
            "function",
            "class",
            "module",
            "routing",
            "policy",
            "trace",
            "hook",
            "memory",
        )
    )


def _code_navigation_plan(
    *,
    task: str,
    kind: str,
    repo_path: str | None,
    repo_name: str | None,
    project: str | None,
    trace_id: str | None,
    session_id: str | None,
    resume_id: str | None,
) -> dict | None:
    if not _should_use_code_navigation(task, kind):
        return None
    if not mimir_available():
        return None
    resolved_repo_path = repo_path or str(ROOT)
    resolved_repo_name = repo_name or ROOT.name
    try:
        return navigate_for_task(
            task=task,
            repo_path=resolved_repo_path,
            repo_name=resolved_repo_name,
            project=project,
            trace_id=trace_id,
            session_id=session_id,
            resume_id=resume_id,
            limit=6,
        )
    except Exception as exc:
        return {"enabled": False, "error": str(exc), "pack_ids": [], "suggested_files": []}


def apply_budget(required: list[str], recommended: list[str], budget: int | None) -> tuple[list[str], list[str], int]:
    required_tokens = sum(estimate_tokens(path) for path in required)
    if budget is None:
        rec_tokens = sum(estimate_tokens(path) for path in recommended)
        return recommended, [], required_tokens + rec_tokens

    kept: list[str] = []
    excluded: list[str] = []
    total = required_tokens
    for path in recommended:
        cost = estimate_tokens(path)
        if total + cost <= budget:
            kept.append(path)
            total += cost
        else:
            excluded.append(path)
    return kept, excluded, total


def build_plan(
    task: str,
    budget: int | None,
    *,
    repo_path: str | None = None,
    repo_name: str | None = None,
    project: str | None = None,
    trace_id: str | None = None,
    session_id: str | None = None,
    resume_id: str | None = None,
) -> dict:
    kind = classify_task(task)
    route_cfg = ROUTES[kind]
    required = list(REQUIRED)
    if route_cfg["meaningful"]:
        required += OPTIONAL_HOT
    recommended = list(route_cfg["recommended"])

    for area, keywords in INDEX_HINTS.items():
        if any(keyword in task_lower(task) for keyword in keywords):
            if area == "verification":
                recommended += ["scripts/verify.sh", "scripts/select-verification.py"]
            elif area == "governance":
                recommended += ["docs/cold-handoff.md", ".architect/alignment_report.md", ".architect/repository_truth.json"]
            elif area == "mimir":
                recommended += ["docs/mimir-tools.md", "project_knowledge.md"]
            elif area == "contracts":
                recommended += [
                    "contracts/trace.schema.json",
                    "contracts/eval-result.schema.json",
                    "contracts/memory-fact.schema.json",
                    "contracts/hook-event.schema.json",
                    "contracts/session-resume.schema.json",
                    "scripts/validate-contracts.py",
                ]

    recommended += index_hits(task)
    code_navigation = _code_navigation_plan(
        task=task,
        kind=kind,
        repo_path=repo_path,
        repo_name=repo_name,
        project=project,
        trace_id=trace_id,
        session_id=session_id,
        resume_id=resume_id,
    )
    if code_navigation:
        recommended += list(code_navigation.get("suggested_files") or [])
    recommended = [path for path in dedupe(recommended) if path not in required and (ROOT / path).exists()]
    kept, excluded, estimated_tokens = apply_budget(required, recommended, budget)

    return {
        "task": task,
        "task_class": kind,
        "route": route_cfg["route"],
        "meaningful": route_cfg["meaningful"],
        "required": required,
        "recommended": kept,
        "excluded": excluded,
        "estimated_tokens": estimated_tokens,
        "budget": budget,
        "mcp_preflight": list(route_cfg["preflight"]),
        "mcp_actions": list(route_cfg["mcp_actions"]),
        "navigation_preference": [
            "mimir_code_navigation_first",
            "serena_exact_symbol_ops_second",
            "mimir_hybrid_navigation_first",
        ],
        "code_navigation": code_navigation,
        "code_navigation_pack_ids": list((code_navigation or {}).get("pack_ids") or []),
        "navigation_required": bool(route_cfg["meaningful"] and kind not in {"analysis", "trivial_edit"}),
    }


def format_plan(plan: dict) -> str:
    lines = [
        f"Task class: {plan['task_class']}",
        f"Route: {plan['route']}",
        f"Meaningful task: {'yes' if plan['meaningful'] else 'no'}",
        "",
        "MCP preflight:",
    ]
    if plan["mcp_preflight"]:
        lines.extend(f"- {name}" for name in plan["mcp_preflight"])
    else:
        lines.append("- (none)")
    lines += ["", "Required MCP actions:"]
    lines.extend(f"- {item}" for item in plan["mcp_actions"])
    lines += ["", "Navigation order:"]
    lines.extend(f"- {item}" for item in plan.get("navigation_preference") or [])
    lines += ["", "Required files:"]
    lines.extend(f"- {path}" for path in plan["required"])
    lines += ["", "Recommended files:"]
    if plan["recommended"]:
        lines.extend(f"- {path}" for path in plan["recommended"])
    else:
        lines.append("- (none)")
    if plan["excluded"]:
        lines += ["", "Excluded by budget:"]
        lines.extend(f"- {path}" for path in plan["excluded"])
    navigation = plan.get("code_navigation") or {}
    if navigation:
        lines += ["", "Code navigation:"]
        if navigation.get("error"):
            lines.append(f"- unavailable: {navigation['error']}")
        else:
            lines.append(f"- snapshot: {navigation.get('snapshot_id') or 'unknown'}")
            if plan.get("code_navigation_pack_ids"):
                lines.append(f"- pack ids: {', '.join(plan['code_navigation_pack_ids'])}")
            for item in navigation.get("top_results") or []:
                symbol = item.get("qualname") or item.get("symbol_name") or "unknown"
                path = item.get("path") or "unknown"
                lines.append(f"- {symbol} [{path}]")
    lines += ["", f"Estimated tokens: {plan['estimated_tokens']}"]
    return "\n".join(lines)


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def self_check() -> None:
    plan = build_plan("analyze the governance contract and mcp flow", None)
    _assert(plan["task_class"] == "analysis", "analysis task misclassified")
    _assert(plan["route"] == "direct-analysis", "analysis route mismatch")
    _assert("mimir" in plan["mcp_preflight"], "analysis must use Mimir preflight")

    plan = build_plan("fix typo in readme wording", None)
    _assert(plan["task_class"] == "trivial_edit", "trivial edit misclassified")
    _assert(plan["route"] == "direct-trivial", "trivial route mismatch")

    plan = build_plan("implement safer memory recall routing", None)
    _assert(plan["task_class"] == "governance", "governance task misclassified")
    _assert(plan["route"] == "direct-governance", "governance route mismatch")
    _assert("docs/mimir-tools.md" in plan["recommended"], "governance task missing Mimir doc")

    plan = build_plan("add a new feature to this repo", None)
    _assert(plan["task_class"] == "bounded_implementation", "implementation misclassified")
    _assert(plan["route"] == "direct-implementation", "implementation route mismatch")
    _assert("mimir" in plan["mcp_preflight"], "implementation must include Mimir")

    plan = build_plan("change schema and api across multiple files", None)
    _assert(plan["task_class"] == "risky_change", "risky task misclassified")
    _assert(plan["route"] == "governed-direct", "risky route mismatch")
    _assert("mimir" in plan["mcp_preflight"], "risky task must include Mimir")

    tiny_budget = max(1, sum(estimate_tokens(path) for path in REQUIRED) // 2)
    plan = build_plan("implement safer memory recall routing", tiny_budget)
    _assert(plan["required"][:4] == REQUIRED, "required files changed under budget trim")
    _assert(isinstance(plan["recommended"], list), "recommended must remain a list")

    serialized = json.loads(json.dumps(build_plan("implement safer memory recall routing", 12000)))
    _assert("code_navigation_pack_ids" in serialized, "plan missing code navigation pack ids field")
    _assert("navigation_required" in serialized, "plan missing navigation_required field")
    print("select-context self-check: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description="Select task route and repo context.")
    parser.add_argument("task", nargs="?", default="", help="Task description")
    parser.add_argument("--budget", type=int, default=None, help="Max estimated token budget")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Emit JSON")
    parser.add_argument("--repo-path", default=None, help="Repo path to index for code navigation")
    parser.add_argument("--repo-name", default=None, help="Logical repo name for code navigation")
    parser.add_argument("--project", default=None, help="Project slug for Mimir lookups")
    parser.add_argument("--trace-id", default=None, help="Trace id for navigation pack lineage")
    parser.add_argument("--session-id", default=None, help="Session id for navigation pack lineage")
    parser.add_argument("--resume-id", default=None, help="Resume id for navigation pack lineage")
    parser.add_argument("--self-check", action="store_true", help="Run built-in checks")
    args = parser.parse_args()

    if args.self_check:
        try:
            self_check()
        except AssertionError as exc:
            print(f"select-context self-check: FAIL — {exc}", file=sys.stderr)
            return 1
        return 0

    if not args.task.strip():
        parser.error("task description required unless --self-check is used")

    plan = build_plan(
        args.task.strip(),
        args.budget,
        repo_path=args.repo_path or os.environ.get("AGENT_REPO_PATH"),
        repo_name=args.repo_name or os.environ.get("AGENT_REPO_NAME"),
        project=args.project or os.environ.get("AGENT_PROJECT"),
        trace_id=args.trace_id or os.environ.get("AGENT_TRACE_ID"),
        session_id=args.session_id or os.environ.get("AGENT_SESSION_ID"),
        resume_id=args.resume_id or os.environ.get("AGENT_RESUME_ID"),
    )
    if args.as_json:
        print(json.dumps(plan, indent=2))
    else:
        print(format_plan(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
