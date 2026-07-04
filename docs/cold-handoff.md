# Cold Handoff — purple_halo

**Last updated:** 2026-07-02

## What this repo is

`purple_halo` is a research repo for building a minimal autonomous product-build loop inside the governed agent harness.

The intended loop is:

```text
goal -> repo/status analysis -> online research -> plan -> implement -> verify -> persist state -> schedule next run -> repeat
```

The point of the repo is to test how far a trusted, minimally restricted, agent-driven software loop can go when it is allowed to keep iterating until the product goal is fully realized.

## Current state

- governed runtime substrate: installed
- rules/hooks/contracts/scripts: present
- Mimir + Serena integration: present
- project goal: defined
- actual loop implementation: not started yet

This repo is product-bootstrap-complete from a governance perspective, but product-empty from a loop-implementation perspective.

## Immediate next build target

Build the smallest working cycle that can:

1. read `project_goals.md`
2. inspect repo state and `project_status.md`
3. perform targeted external research
4. generate a next-step plan
5. execute a bounded implementation
6. verify the change
7. record state for the next cycle

## What not to do next

- Do not build a large orchestration framework before proving one working cycle.
- Do not over-gate the system into human-heavy approvals.
- Do not mistake installed kernel assets for finished product functionality.
- Do not let repo-truth files drift back into agent-kernel identity.

## Core repo-truth files

- `project_goals.md` — mission and success criteria
- `project_status.md` — actual current state
- `repo_map.md` — local layout and navigation
- `project_learning/active.md` — validated repo-local lessons

## Runtime substrate references

Use the installed governed assets as infrastructure:

- `AGENTS.md`
- `.cursor/rules/`
- `.cursor/hooks.json`
- `scripts/cursor_session.py`
- `scripts/session_orchestrator.py`
- `scripts/select-context.py`
- `scripts/select-verification.py`

These help drive the loop, but they are not the loop itself.
