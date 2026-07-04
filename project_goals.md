# Project Goals

## Product Goal

Build `purple_halo` as a minimal research system that runs a self-updating product-build loop inside the governed harness.

The system starts from a user-defined project goal and repeatedly:

1. analyzes the goal
2. analyzes the current repository and project status
3. performs online research relevant to the goal and current state
4. creates the next implementation plan
5. executes the plan in full using agents
6. verifies the work actually landed and behaves correctly
7. re-examines the goal, repo, status, and research state
8. generates the next plan
9. continues the cycle until the product goal is fully realized end to end

This loop must be schedulable by day and time, so operators can choose how many times per day it runs and at what times.

This project is primarily a research system. The purpose is to learn how far a trusted agent-driven build loop can go when it is allowed to operate with minimal restriction, minimal gating, and strong reliance on autonomous agent execution.

## Success Criteria

- The system accepts a high-level product goal as the durable mission for the project.
- The system maintains a current project status derived from the real repository state rather than stale prose alone.
- Each cycle can inspect the repo, inspect prior outputs, inspect current status, and decide what to do next.
- Each cycle can perform online research relevant to the current goal and status before planning work.
- Each cycle produces an explicit plan for the next increment of work.
- The implementation step can execute multi-step product work through agents rather than a single rigid script path.
- The verification step checks whether planned work was actually completed and whether it behaves as intended.
- The loop records enough state to continue from the latest verified point instead of restarting blindly.
- Operators can configure the daily run schedule, including how many runs per day and at what times they execute.
- The system can continue iterating without manual prompting between cycles unless it hits a hard blocker.
- The loop can stop only when the goal is fully realized, a real blocker appears, or an operator pauses it.
- The project remains minimal: the loop should be as small and understandable as possible while still completing the full cycle.

## Non Goals

- Building a heavily gated approval workflow that requires human sign-off at every stage.
- Optimizing first for enterprise safety, compliance, or conservative release controls.
- Turning the system into a generic project-management dashboard before the core loop works.
- Creating a large orchestration framework with unnecessary abstraction before proving the minimal loop.
- Replacing real repo inspection and verification with prose-only summaries.
- Treating the system as a benchmark toy instead of a real autonomous product-build experiment.

## Architecture Principles

- Agent-first execution over rigid workflow scripting.
- Minimal structure, strong loop continuity.
- Real repo state over aspirational documentation.
- Research and planning must be refreshed every cycle, not assumed once.
- Verification must be based on actual implementation evidence.
- Scheduling must be operator-controlled, but execution inside each run should be autonomous.
- The system should prefer iteration and adaptation over static long-range plans.
- Every cycle should leave behind enough traceable state to support the next one.
- Keep the product small until the end-to-end loop is genuinely working.

## Module Ownership Rules

- `project_goals.md` defines the durable mission and success criteria for `purple_halo`.
- `project_status.md` reflects the current actual state of the repo and loop.
- `repo_map.md` explains the current local structure of the project.
- `project_learning/` stores live research findings, loop lessons, and evolving operating knowledge.
- `scripts/` should contain the executable loop, scheduling, planning, verification, and continuity logic.
- `contracts/` should define any durable state, trace, plan, or verification artifacts the loop depends on.
- `.cursor/rules/` and `AGENTS.md` define how agents are expected to behave while building the system.

## Required Testing

- Verify that a scheduled or manually triggered cycle can run end to end.
- Verify that the cycle can inspect repo state, create a plan, implement work, and verify the result.
- Verify that loop state persists correctly between cycles.
- Verify that the scheduler obeys configured run frequency and time windows.
- Verify that research, planning, implementation, and verification outputs are stored in a reusable way.
- Verify that the system can resume after interruption without losing the current mission or status.

## Drift Definition

- The loop stops doing research before planning.
- The loop plans work without checking the current repo or current status.
- The loop claims progress without verification evidence.
- The loop becomes primarily human-driven instead of agent-driven.
- The system becomes overly rigid, over-gated, or approval-heavy relative to the research goal.
- The repo mission drifts away from autonomous iterative product realization.
- Scheduling exists but the system cannot actually execute useful autonomous work on schedule.

## Long Term Vision

Create a minimal but real autonomous build loop that can take a product goal, repeatedly improve the product through research, planning, implementation, and verification, and continue doing so on a schedule until the product is fully realized. The broader research aim is to discover what a mostly unrestricted, trusted, agent-run software creation loop can actually achieve in practice.

## Repository Maturity Level

Level 0.2 — bootstrap research repo. Mission defined; autonomous loop not yet implemented.
