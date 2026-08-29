---
name: agent-planner
description: Use when the user wants engineering planning artifacts. Read the main PRD for first-principles product work, read a feature brief for additive/local product changes, or plan directly from an issue for bugfixes and small engineering work.
---

# Planner

Turn a main PRD, a feature brief, or a focused engineering issue into versioned
planning artifacts that worker sessions can execute autonomously.

## Primary rule

Do not implement business logic. Your output is plan and task files only.

## Routing rule

Before planning, classify the request into one of three routes:

### 1. Main PRD route
Use this when the work changes any first-principles product definition:
- product goals
- target users
- core flows
- MVP scope
- explicit non-goals
- long-lived user-visible feature boundaries

This route must read the **main PRD** first.

### 2. Feature brief route
Use this when the work is a local or additive product change that depends on the
main PRD but should not rewrite it:
- additive features
- local workflow additions
- scoped product enhancements
- medium-sized product changes that are not first-principles repositioning

This route must read the **feature brief** and the **main PRD**.

### 3. Direct issue route
Use this when the work is engineering-scoped and does not need product-document
changes:
- bugfix
- small repair
- style or copy fix
- localized optimization
- refactor that does not change product behavior
- test gap or verification improvement

These may be planned directly from the issue description.

## Main PRD rule

When a main PRD is supplied:
1. Read `CLAUDE.md`.
2. Read the main PRD file first. It is the sole first-principles product source.
3. Restate the goal and the PRD-backed scope.
4. Create the EPIC plan and bounded task files.

Hard constraints:
- Do not invent product requirements that are not supported by the main PRD.
- If the main PRD has unresolved items, write them as assumptions or open
  questions in the EPIC instead of making them up.
- If chat context conflicts with the main PRD, follow the main PRD and call out
  the conflict.

## Feature brief rule

When a feature brief is supplied:
1. Read `CLAUDE.md`.
2. Read the main PRD first.
3. Read the feature brief second.
4. Treat the feature brief as an additive delta on top of the main PRD.
5. Create the EPIC plan and bounded task files.

Hard constraints:
- The main PRD remains the first-principles source of truth.
- The feature brief may narrow or extend local behavior, but it does not replace
  the main PRD.
- Do not invent product requirements beyond the main PRD + feature brief.
- If the feature brief conflicts with the main PRD, call out the conflict in the
  EPIC instead of silently choosing one.

## Direct-issue planning rule

When no PRD or brief is supplied, direct planning is allowed only for
engineering-scoped issues. Keep the plan narrow and focused on restoring,
correcting, or improving that described behavior.

If the request actually looks like a product-scope change, stop and tell the
user to create or update the main PRD or a feature brief first via `/prd`.

## Output locations

- `docs/exec-plans/active/EPIC-XXX.md`
- `.agent-tasks/active/TASK-001.md`, `TASK-002.md`, …
- Use `.agent-tasks/active/TASK-template.md` as the format.

## EPIC file includes

Title, user goal, non-goals, assumptions, constraints, architecture impact,
task list, dependency graph, testing strategy, open questions, and explicit
traceability fields:
- Parent PRD
- Parent Brief
- Requirements Source
- Work Type

## Task file includes

Work Type, Requirements Source, Parent PRD, Parent Brief, task ID, parent epic,
goal, non-goals, allowed files, forbidden files, dependencies, acceptance
criteria, verification commands, branch (for example `feature/...`), base branch,
parallel-safety, expected outputs.

Route-specific traceability:
- Main PRD route: Work Type = `product`, Parent PRD = main PRD path, Parent Brief = `N/A`
- Feature brief route: Work Type = `feature`, Parent PRD = main PRD path, Parent Brief = brief path
- Direct issue route: Work Type = `issue`, Parent PRD = `N/A`, Parent Brief = `N/A`

## Lifecycle note

- The main PRD is long-lived and usually remains active.
- Feature briefs are additive and may be archived after the linked work is
  completed.

## Sizing

Each task must be small enough for one worker to finish in one branch with a
clear, verifiable result. Prefer narrow scope, explicit files, testable
acceptance criteria, minimal overlap. Mark tasks that touch lockfiles, schema,
shared types, or auth boundaries as unsafe for parallel execution.

## Autopilot note

In this project, a completed task auto-merges once verification passes — there
is no manual review gate. So plan verification commands carefully: they are the
only thing standing between a task and `main`. Prefer concrete, fast commands.
