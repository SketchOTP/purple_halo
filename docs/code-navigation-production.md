# Code Navigation — Production Usage

Status: **active**. The deliverable is Mimir indexing, materialization, and hybrid retrieval — not dashboard graph UI.

Canonical Mimir ops doc: `/home/sketch/Projects/mimir/docs/CODE_NAVIGATION_PRODUCTION.md`

## Architecture

```text
code_index_repo
  → commit-scoped symbols + references + embeddings

POST /api/projects/{slug}/code-index
  → index + materialize project-intelligence graph

code_search_hybrid / code_blast_radius / code_resume_context
  → bounded context packs for agents

session_orchestrator + cursor_session.py
  → governance hooks record retrieval outcomes
```

## Environment

Shell scripts and `scripts/mimir_code_nav.py` read:

| Variable | Default | Purpose |
|---|---|---|
| `MIMIR_ENDPOINT` | unset | Mimir API base URL |
| `MIMIR_API_KEY` | unset | Bearer auth for API/MCP |

Operator file (not in repo): `~/.config/mimir/env`

```bash
export MIMIR_ENDPOINT=http://127.0.0.1:8787
export MIMIR_API_KEY=...
```

## Global Cursor setup (any workspace)

```bash
./scripts/install-global-cursor-stack.sh
```

This installs canonical `~/.cursor/mcp.json`, removes stale MCPs (`cocoindex-code`, `codebase-memory-mcp`, `headroom`, `gitmcp`), and installs global navigation rules.

Then:

```bash
${EDITOR:-nano} ~/.config/mimir/env   # set MIMIR_API_KEY
# Restart Cursor
./scripts/verify-code-navigation-production.sh
```

## Repo setup handoffs (give to AI coders)

| Task | File |
|---|---|
| New repo | `new_repo_instructions.md` |
| Existing repo | `existing_repo_instructions.md` |

## Production bootstrap (this repo)

```bash
./scripts/setup-code-navigation-production.sh
./scripts/verify-code-navigation-production.sh
```

Setup indexes `agent` (and optionally `mimir`), materializes project graph, and smoke-tests hybrid search.

## Agent session flow

Non-trivial code work in Cursor:

```text
memory_recall
→ code_index_repo (repo_path = workspace root)
→ code_search_hybrid (query = task)
→ code_blast_radius (top symbol)
→ Serena symbol tools if needed
→ cursor_session.py tool adapters for governed execution
→ verify
→ memory_record_outcome
```

CLI helper:

```bash
MIMIR_ENDPOINT=... MIMIR_API_KEY=... python3 scripts/mimir_code_nav.py \
  --task "how does session orchestrator enforce navigation"
```

## Do not optimize for

- 3D graph readability, label toggles, or dashboard FPS
- Empty graph UI when traces/evals are not stored yet

## Do optimize for

- Index freshness after meaningful repo changes
- Hybrid search first-hit quality
- Blast-radius accuracy before edits
- Retrieval pack IDs flowing into traces/evals

See also: `docs/codebase-navigation-plan.md`, `docs/mimir-tools.md`, `docs/usage/mcp_cursor.md`.
