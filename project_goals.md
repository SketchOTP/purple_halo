# Project Goals

## Product Goal

Build **Purple Halo** as a **drop-in autonomous loop** for any repository.

An operator installs Purple Halo into a repo, writes a **mission** (`project_goals.md`), sets a **schedule**, and presses **Play**. Purple Halo then repeatedly works toward that mission without manual prompting between runs:

1. reads the operator mission and current repo state
2. researches what is relevant to the mission and repo
3. plans the next increment of product work (not loop infrastructure)
4. executes the plan through agents
5. verifies the work landed and behaves correctly
6. persists state and continues on schedule until the mission is done, blocked, or paused

The **master checkout** (`purple_halo` repo) exists to ship and dogfood this drop-in product: installer, operator UI, scheduler, and loop engine. Installed instances use the same stack in **`project_mode`** to work on **their** repo's mission.

## Success Criteria

- Operator can install Purple Halo into another repo with mission + schedule copied from master.
- Installed instance UI shows mission, schedule, Play/Pause, and run progress — no Install panel.
- Each scheduled cycle inspects the repo, plans product work, executes via agents, and verifies results.
- Loop state persists between cycles so work continues from the last verified point.
- Scheduler supports interval and specific times (weekdays, timezone, every-N-weeks).
- Runs stop only when the mission is achieved, a real blocker appears, or the operator pauses.
- `project_mode` instances are not subject to self-product production hold, architecture freeze, or goal-delivery machinery.

## Non Goals

- Building purple_halo primarily as a research platform that works on its own loop scripts.
- Heavy approval workflows or enterprise gating on installed instances.
- Seeding mission text from the target repo's native docs (`North_star.md`, etc.) — mission is what the operator gives Purple Halo to do.
- A large orchestration framework before the drop-in loop works end to end on a real repo.

## Architecture Principles

- **Product A first:** installed `project_mode` is the happy path; master is installer + dogfood.
- Agent-first execution over rigid workflow scripting.
- Real repo state over aspirational documentation.
- Mission (`project_goals.md`) is Purple Halo's task, separate from the host project's own docs.
- Scheduling is operator-controlled; execution inside each run is autonomous.
- Keep the operator surface minimal: Mission → Schedule → Play → Progress.

## Module Ownership Rules

- `project_goals.md` — operator mission for this Purple Halo instance.
- `project_status.md` — current repo + loop state derived from evidence.
- `repo_map.md` — layout of the host repo (product code, not loop scripts).
- `project_memory/runtime/` — schedule, history, cycle artifacts.
- `scripts/` — loop engine (copied on install; master maintains the canonical version).
- `operator_ui/` — simple operator UI.

## Required Testing

- Install into a sample repo; mission and schedule copy correctly; instance starts paused.
- Play → due slot → one cycle runs against host product files, not `scripts/loop_*` maintenance.
- Pause stops scheduled runs; Play resumes.
- State persists across service restart.

## Drift Definition

- Installed instances spend cycles on loop infrastructure instead of the operator mission.
- Self-product machinery (production hold, architecture freeze, goal delivery) applies to `project_mode`.
- Mission is confused with the host repo's native documentation.
- Scheduling works but cycles produce no useful product progress.
- Operator UI grows beyond Mission / Schedule / Play / Progress.

## Long Term Vision

Drop Purple Halo into any repo, give it a mission and a schedule, and let it work autonomously until the mission is realized — with a small, understandable codebase and operator surface.

## Repository Maturity Level

Level 0.5 — drop-in installer and operator UI exist; `project_mode` path being simplified and proven on real repos.