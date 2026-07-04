# Mimir MCP Tools

On-demand reference. Load when using durable memory, code navigation, session outcomes, recall quality, or workflow continuity.

Core rule in `AGENTS.md` §7: **commit chat history to Mimir MCP memory** means concise session outcome via `memory_record_outcome`, not a raw transcript.

## Mimir vs agent governance

| System | Role |
|---|---|
| **Mimir** | Recall, code navigation, session outcomes, durable constraints, reusable lessons |
| **Agent repo** | Approval gates, hooks, traces/evals, bounded execution (`cursor_session.py`) |

Mimir remembers and retrieves. Agent governs execution.

## Required Mimir Flow

For meaningful work, Mimir is mandatory unless unavailable.

### At task start

1. `memory_recall`
2. `project_status_summary` if the task is dormant or unclear
3. `memory_search` before changing an existing system or repeated workflow
4. `code_index_repo` when the workspace repo may be stale or unindexed
5. `code_search_hybrid` before broad code exploration on non-trivial code changes
6. `code_blast_radius` when a target symbol or candidate edit surface is identified

### During work

- `code_resolve_symbol` for exact Mimir-side symbol lookup
- `code_find_callers` and `code_find_tests` for targeted impact and validation search
- `code_resume_context` to restore latest cross-machine code-navigation state
- `memory_remember` for durable discoveries
- `reflection_log` after repeated failures or high-value lessons
- `retrieval_stats` when diagnosing weak recall quality
- `telemetry_snapshot` before making health or optimization claims

### At completion

1. run verification
2. inspect final diff
3. call `memory_record_outcome`

Store:

- task
- result
- changed files
- verification
- route used
- trace id/path if any
- eval id/path if any
- Architect status if any
- blockers
- next step

Never store secrets, credentials, `.env`, API keys, raw dumps, full files, private user data, or raw transcripts.

## If Mimir Is Blocked

- say it is blocked
- include the exact reason
- include a concise manual outcome in the final response
- do not claim memory was recorded

## Tool Guidance

| Tool | When to use |
|---|---|
| `memory_recall` | Meaningful task start |
| `memory_search` | Before changing something that may already exist |
| `code_index_repo` | Build/refresh commit-scoped index for workspace repo |
| `code_search_hybrid` | First-pass code navigation for non-trivial repo work |
| `code_blast_radius` | Bound impact before editing |
| `code_resolve_symbol` | Exact symbol resolution from Mimir index |
| `code_find_callers` | See which symbols invoke the target |
| `code_find_tests` | Find likely validation coverage |
| `code_resume_context` | Restore latest retrieval packs and resume context |
| `project_bootstrap` | Auto-seed project capsules from repo intelligence |
| `memory_remember` | Durable discovery during work |
| `memory_record_outcome` | End of every meaningful session |
| `retrieval_stats` | Diagnose recall quality |
| `project_status_summary` | Restore project awareness |
| `reflection_log` | Repeated failures or prevention-worthy lessons |

**Project materialization (index + intelligence graph):** `POST /api/projects/{slug}/code-index?force=true` — substrate for agents; dashboard graph is optional UI.

Production setup: `docs/code-navigation-production.md`

Preferred navigation order:

1. Mimir code navigation
2. Serena exact symbol tools

Setup handoffs: `existing_repo_instructions.md`, `new_repo_instructions.md`

Use all available MCPs at task start. Do not rely on chat memory alone.
