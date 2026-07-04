# Serena MCP Tools

On-demand reference. Load when inspecting unfamiliar code, tracing symbols, or performing surgical edits.

Serena provides **symbol-aware code navigation** via LSP-backed tools. Use before modifying code you have not read. Complements cocoindex-code semantic search (see `docs/cocoindex-code.md`).

Configured globally in `~/.cursor/mcp.json` (server key `serena`).

---

## When to Use Serena

| Situation | Prefer Serena |
|---|---|
| Find a class, method, or function by name | `find_symbol`, `find_declaration` |
| Understand file/module structure | `get_symbols_overview` |
| Trace callers or references | `find_referencing_symbols` |
| Find interface implementations | `find_implementations` |
| Surgical symbol edit or rename | `replace_symbol_body`, `rename_symbol` |
| Broad "how does auth work?" discovery | Use cocoindex-code `search` first |

**Task start:** Call `initial_instructions` once per session before other Serena tools.

---

## Exposed Tools (28)

### Setup / Project

| Tool | When to use |
|---|---|
| `initial_instructions` | **First** — read Serena Instructions Manual |
| `activate_project` | Activate Serena for the workspace project |
| `onboarding` | First-time Serena project setup |
| `get_current_config` | Inspect Serena configuration |

### Symbol Navigation

| Tool | When to use |
|---|---|
| `find_symbol` | Locate symbols by name path pattern |
| `find_declaration` | Jump to symbol declaration |
| `find_referencing_symbols` | Find all references to a symbol |
| `find_implementations` | Find implementations of interface/abstract symbols |
| `get_symbols_overview` | File-level symbol tree overview |
| `get_diagnostics_for_file` | LSP diagnostics for a file |

### Search / Files

| Tool | When to use |
|---|---|
| `search_for_pattern` | Regex/pattern search within project |
| `find_file` | Locate files by name or pattern |
| `read_file` | Read file contents via Serena |
| `list_dir` | List directory contents |

### Symbol Editing

| Tool | When to use |
|---|---|
| `replace_symbol_body` | Replace entire symbol body |
| `insert_before_symbol` | Insert code before a symbol |
| `insert_after_symbol` | Insert code after a symbol |
| `rename_symbol` | Rename symbol across codebase |
| `safe_delete_symbol` | Delete symbol with reference safety |
| `replace_content` | Replace file content regions |
| `create_text_file` | Create new file |

### Serena Memory (project-local notes)

| Tool | When to use |
|---|---|
| `write_memory` | Store Serena-local project note |
| `read_memory` | Read Serena-local note |
| `list_memories` | List Serena-local notes |
| `edit_memory` | Edit Serena-local note |
| `delete_memory` | Delete Serena-local note |
| `rename_memory` | Rename Serena-local note |

### Shell

| Tool | When to use |
|---|---|
| `execute_shell_command` | Run shell command via Serena (use sparingly) |

---

## Code Inspection Workflow

```text
1. initial_instructions
2. activate_project (if needed)
3. get_symbols_overview (target file) OR find_symbol (known name)
4. find_referencing_symbols / find_declaration (trace impact)
5. read_file (narrow regions only)
6. Make smallest safe change
7. get_diagnostics_for_file (post-edit check)
```

For unfamiliar features, start with cocoindex-code `search`, then switch to Serena for symbol-level grounding.

---

## Symbol / Refactor / Search Guidance

- **Name paths:** `ClassName/methodName`; append `[0]` for overloads; prefix `/` for absolute path within file.
- **Depth:** Use `depth > 0` on `find_symbol` to include children (e.g., class methods).
- **Scope:** Pass `relative_path` to limit search to a file or directory.
- **Renames:** Prefer `rename_symbol` over manual search-replace for cross-file renames.
- **Deletes:** Use `safe_delete_symbol` to check references before removal.

Serena memory tools are for Serena-local navigation notes — **not** a substitute for Mimir durable memory.

---

## Cursor Adapter

`.cursor/rules/06-serena.mdc` — pointer only.
