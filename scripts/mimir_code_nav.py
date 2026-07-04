#!/usr/bin/env python3
"""Shared Mimir code-navigation client for agent runtime scripts."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MIMIR_ENV_FILE = Path(os.environ.get("MIMIR_ENV_FILE", Path.home() / ".config" / "mimir" / "env"))
_mimir_env_loaded = False


def _ensure_mimir_env_loaded() -> None:
    """Load ~/.config/mimir/env when hooks/shell omit MIMIR_ENDPOINT (Cursor does not source it)."""
    global _mimir_env_loaded
    if _mimir_env_loaded or os.environ.get("MIMIR_ENDPOINT"):
        _mimir_env_loaded = True
        return
    if not MIMIR_ENV_FILE.is_file():
        _mimir_env_loaded = True
        return
    for line in MIMIR_ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip("\"").strip("\047")
    _mimir_env_loaded = True


def _endpoint() -> str:
    _ensure_mimir_env_loaded()
    return os.environ.get("MIMIR_ENDPOINT", "").rstrip("/")


def _api_key() -> str | None:
    _ensure_mimir_env_loaded()
    return os.environ.get("MIMIR_API_KEY")


def load_mimir_env() -> None:
    """Public entry: load ~/.config/mimir/env when Cursor hooks omit exports."""
    _ensure_mimir_env_loaded()


load_mimir_env()


def mimir_available() -> bool:
    return bool(_endpoint())


def _post_json(url: str, payload: dict[str, Any], api_key: str | None) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_mcp_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    endpoint = _endpoint()
    if not endpoint:
        raise RuntimeError("MIMIR_ENDPOINT is not set")
    response = _post_json(
        f"{endpoint}/mcp",
        {
            "jsonrpc": "2.0",
            "id": f"agent-{name}",
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments,
            },
        },
        _api_key(),
    )
    if "error" in response:
        raise RuntimeError(str(response["error"]))
    result = dict(response.get("result") or {})
    content = list(result.get("content") or [])
    if not content:
        return result
    text = str((content[0] or {}).get("text") or "")
    if not text:
        return result
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def _call_code_nav(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    endpoint = _endpoint()
    if not endpoint:
        raise RuntimeError("MIMIR_ENDPOINT is not set")
    return (_post_json(f"{endpoint}{path}", payload, _api_key()).get("payload") or {})


def _api_repo_path(repo_path: str) -> str:
    path = Path(repo_path).expanduser().resolve()
    host_root = os.environ.get("MIMIR_HOST_REPO_ROOT", "").strip()
    container_root = os.environ.get("MIMIR_CONTAINER_REPO_ROOT", "").strip()
    if host_root and container_root:
        host = Path(host_root).expanduser().resolve()
        try:
            rel = path.relative_to(host)
            return str(Path(container_root) / rel)
        except ValueError:
            pass
    return str(path)


def index_repo(*, repo_path: str, repo_name: str | None, project: str | None, force: bool = False) -> dict[str, Any]:
    return _call_code_nav(
        "/api/code-navigation/index",
        {
            "repo_path": _api_repo_path(repo_path),
            "repo_name": repo_name,
            "project": project,
            "force": force,
        },
    )


def search_hybrid(
    *,
    query: str,
    repo_name: str | None,
    snapshot_id: str | None = None,
    project: str | None = None,
    trace_id: str | None = None,
    session_id: str | None = None,
    resume_id: str | None = None,
    limit: int = 6,
) -> dict[str, Any]:
    return _call_code_nav(
        "/api/code-navigation/search-hybrid",
        {
            "query": query,
            "repo_name": repo_name,
            "snapshot_id": snapshot_id,
            "project": project,
            "trace_id": trace_id,
            "session_id": session_id,
            "resume_id": resume_id,
            "limit": limit,
        },
    )


def blast_radius(*, symbol: str, repo_name: str | None, snapshot_id: str | None = None) -> dict[str, Any]:
    return _call_code_nav(
        "/api/code-navigation/blast-radius",
        {
            "symbol": symbol,
            "repo_name": repo_name,
            "snapshot_id": snapshot_id,
        },
    )


def memory_record_outcome(
    *,
    content: str,
    result: str,
    project: str | None = None,
    session_id: str | None = None,
    lesson: str | None = None,
    task_outcome: str | None = None,
    has_correction: bool = False,
    has_harmful_outcome: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "content": content,
        "result": result,
        "project": project,
        "session_id": session_id,
        "has_correction": has_correction,
        "has_harmful_outcome": has_harmful_outcome,
    }
    if lesson:
        payload["lesson"] = lesson
    if task_outcome:
        payload["task_outcome"] = task_outcome
    return _post_mcp_tool("memory_record_outcome", payload)


def _get_json(url: str, api_key: str | None) -> dict[str, Any]:
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def navigate_graph_enriched(
    *,
    task: str,
    repo_path: str,
    repo_name: str,
    project: str | None = None,
    trace_id: str | None = None,
    session_id: str | None = None,
    resume_id: str | None = None,
    limit: int = 6,
) -> dict[str, Any]:
    """Hybrid retrieval expanded with materialized graph neighbors when available."""
    base = navigate_for_task(
        task=task,
        repo_path=repo_path,
        repo_name=repo_name,
        project=project,
        trace_id=trace_id,
        session_id=session_id,
        resume_id=resume_id,
        limit=limit,
    )
    slug = project or repo_name
    endpoint = _endpoint()
    graph_files: list[str] = []
    if endpoint and slug:
        try:
            graph_payload = _get_json(
                f"{endpoint}/api/projects/{urllib.parse.quote(slug)}/graph/context?query={urllib.parse.quote(task)}&limit={limit}",
                _api_key(),
            )
            for item in graph_payload.get("files") or graph_payload.get("nodes") or []:
                path = item.get("path") if isinstance(item, dict) else str(item)
                if path:
                    graph_files.append(path)
        except Exception:
            graph_files = []
    blast = base.get("blast_radius") or {}
    for bucket in ("callers", "callees", "tests"):
        for item in blast.get(bucket) or []:
            path = item.get("path")
            if path:
                graph_files.append(path)
    seen: set[str] = set()
    merged_files: list[str] = []
    for path in list(base.get("suggested_files") or []) + graph_files:
        if not path or path in seen:
            continue
        seen.add(path)
        merged_files.append(path)
    base["suggested_files"] = merged_files
    base["retrieval_mode"] = "graph_enriched"
    base["graph_file_count"] = len(graph_files)
    return base


def navigate_for_task(
    *,
    task: str,
    repo_path: str,
    repo_name: str,
    project: str | None = None,
    trace_id: str | None = None,
    session_id: str | None = None,
    resume_id: str | None = None,
    limit: int = 6,
) -> dict[str, Any]:
    index_result = index_repo(repo_path=repo_path, repo_name=repo_name, project=project, force=False)
    hybrid_result = search_hybrid(
        query=task,
        repo_name=repo_name,
        snapshot_id=index_result.get("snapshot_id"),
        project=project,
        trace_id=trace_id,
        session_id=session_id,
        resume_id=resume_id,
        limit=limit,
    )
    top_results = list(hybrid_result.get("results") or [])
    top_symbol = None
    if top_results:
        first = top_results[0]
        top_symbol = first.get("qualname") or first.get("symbol_name")
    blast_result = blast_radius(
        symbol=top_symbol,
        repo_name=repo_name,
        snapshot_id=hybrid_result.get("snapshot_id") or index_result.get("snapshot_id"),
    ) if top_symbol else None
    suggested_files = [item.get("path") for item in top_results if item.get("path")]
    if blast_result:
        suggested_files.extend(item.get("path") for item in blast_result.get("callers") or [] if item.get("path"))
        suggested_files.extend(item.get("path") for item in blast_result.get("tests") or [] if item.get("path"))
    seen: set[str] = set()
    deduped_files: list[str] = []
    for path in suggested_files:
        if not path or path in seen:
            continue
        seen.add(path)
        deduped_files.append(path)
    return {
        "enabled": True,
        "snapshot_id": index_result.get("snapshot_id") or hybrid_result.get("snapshot_id"),
        "pack_ids": [hybrid_result.get("pack_id")] if hybrid_result.get("pack_id") else [],
        "query": task,
        "top_results": top_results[:4],
        "blast_radius": blast_result or None,
        "suggested_files": deduped_files,
    }


def _self_check() -> int:
    if not mimir_available():
        print("mimir-code-nav: PASS (MIMIR_ENDPOINT unavailable; client wiring only)")
        return 0
    payload = navigate_for_task(
        task="trace writer implementation",
        repo_path=str(ROOT),
        repo_name=ROOT.name,
        project=os.environ.get("AGENT_PROJECT"),
        trace_id=os.environ.get("AGENT_TRACE_ID"),
        session_id=os.environ.get("AGENT_SESSION_ID"),
        resume_id=os.environ.get("AGENT_RESUME_ID"),
    )
    assert isinstance(payload.get("suggested_files"), list)
    assert "pack_ids" in payload
    print("mimir-code-nav: PASS")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Shared Mimir code navigation client.")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--task")
    parser.add_argument("--repo-path", default=str(ROOT))
    parser.add_argument("--repo-name", default=ROOT.name)
    parser.add_argument("--project")
    parser.add_argument("--trace-id")
    parser.add_argument("--session-id")
    parser.add_argument("--resume-id")
    args = parser.parse_args(argv)
    if args.self_check:
        return _self_check()
    if not args.task:
        raise SystemExit("task is required unless --self-check is used")
    payload = navigate_for_task(
        task=args.task,
        repo_path=args.repo_path,
        repo_name=args.repo_name,
        project=args.project,
        trace_id=args.trace_id,
        session_id=args.session_id,
        resume_id=args.resume_id,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
