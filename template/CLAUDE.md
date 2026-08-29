# Project Rules — Autopilot Mode

Human steers. Agents execute — **fully autonomously, no human confirmation**.
The repository is the source of truth.

This project runs in autopilot: agents plan, implement, verify, commit, push,
open PRs, and **auto-merge** without asking. The only quality gate is
verification. The only hard guardrail is the secret block (below).

## Autopilot behavior

- **No confirmation prompts.** Permission mode is `bypassPermissions`
  (`.claude/settings.json`). Every tool call proceeds without asking.
- **Auto-merge.** PRs merge into their base branch automatically once
  verification passes. There is no human review step.
- **Main is allowed.** In this repository's explicit autopilot mode, agents may commit,
  push, and merge into `main` (or the detected default branch) when verification passes.
  The Git policy helpers still forbid force-pushing protected branches.
- **Standard topic branches.** Orchestrated work uses `feature/<name>`, `fix/<name>`,
  `refactor/<name>`, or `chore/<name>`; task type determines the default prefix.
- **Git synchronization.** Before a task worktree is created, fetch the base branch;
  before committing, rebase the topic branch onto the latest `origin/<base>`.
- **Conventional Commits.** Automated commits use `feat:`, `fix:`, `docs:`,
  `refactor:`, `style:`, or `chore:` followed by a non-empty description.
- **Worktree cleanup.** After a successful merge, remove the task worktree and its
  local topic branch. On failure, preserve the worktree and record recovery details.
- **Verification is the sole gate.** Detected `lint` / `typecheck` / `test`
  commands must pass before anything is committed. If they fail, fix them
  before stopping — failed verification blocks completion.
- **Isolated task execution.** The orchestrator runs each task in its own git
  worktree on a standard topic branch, then merges.

## The one guardrail: secrets

Never weakened, even in autopilot:

- The Stop hook and orchestrator **refuse to commit** files matching secret
  patterns: `.env*`, `secrets/`, and paths containing `secret` or `token`.
- Remove such files from the change set and proceed. This is the only thing
  that will block an otherwise-verifying change.

Never attempt to disable this check or route secrets around it.

## Workflow

This template supports three planning routes:

### Main PRD route

Use this when the work changes product goals, target users, core flows, MVP
scope, explicit non-goals, or other first-principles product boundaries.

```bash
python orchestrator/agent-team.py plan --from-prd docs/prd/active/PRD-001.md
python orchestrator/agent-team.py run   TASK-001
python orchestrator/agent-team.py status
python orchestrator/agent-team.py integrate
```

### Feature brief route

Use this when the work is an additive or local product enhancement that should
not rewrite the main PRD.

```bash
python orchestrator/agent-team.py plan --from-brief docs/prd/changes/active/FEATURE-001.md
python orchestrator/agent-team.py run   TASK-001
python orchestrator/agent-team.py status
python orchestrator/agent-team.py integrate
```

### Direct issue route

Use this for bugfixes, small repairs, localized optimizations, style/copy fixes,
or tests that do not change product behavior.

```bash
python orchestrator/agent-team.py plan  "fix login button not responding"
python orchestrator/agent-team.py run   TASK-001
python orchestrator/agent-team.py status
python orchestrator/agent-team.py integrate
```

- `prd` — a product-consultant flow that creates or updates the main PRD, creates feature briefs for additive/local product changes, or routes true bugfixes away from PRD entirely.
- `plan` — reads the main PRD, reads a feature brief plus main PRD, or plans directly from an engineering issue depending on the route.
- `run` — executes one task in a worktree: implement → verify → commit → push →
  open PR → **auto-merge** → move the task to `completed`.
- `status` — shows task counts per state.
- `integrate` — lists any topic-branch PRs that failed to auto-merge.

If a request changes product definition but no main PRD or feature brief is supplied, planning should stop and require `/prd` first.

For ad-hoc interactive work, just edit and stop: the Stop hook verifies,
commits, pushes, and (on a feature branch) auto-merges for you.

## Task files

Each task is a markdown file under `.agent-tasks/` and moves through states:

- `active/` — planned, waiting to run
- `running/` — a worker is executing it
- `pr-opened/` — (reserved; autopilot merges immediately, so rarely used)
- `completed/` — merged
- `failed/` — verification failed, worker errored, or no changes

Start from `.agent-tasks/active/TASK-template.md`. Every task must specify:
work type, requirements source, parent PRD (or `N/A` for direct issue work),
parent brief (or `N/A` unless planning from a feature brief), goal, scope,
allowed files, forbidden files, acceptance criteria, verification commands,
branch (for example `feature/...`, `fix/...`, `refactor/...`, or `chore/...`), and base branch.

## Planning

Complex work is planned before implementation.

- The main product document lives in `docs/prd/active/PRD-001.md`.
- Feature briefs live in `docs/prd/changes/active/` and may later move to `docs/prd/changes/completed/`.
- `docs/prd/completed/` is reserved for rare cases where the main PRD itself is retired or replaced.
- Execution plans live in `docs/exec-plans/active/` (and `completed/`).
- Task files live in `.agent-tasks/active/`.
- Run logs and summaries live in `.agent-runs/`.

When a PRD or feature brief exists for the work, treat it as the single source of truth for
product intent. The planner must read the applicable product document before generating EPIC or TASK
artifacts, and must not invent unsupported product requirements.

## Optional bundled skills

The template also includes optional GitHub-oriented skills:

- `gh-address-comments` — summarize and address review or issue comments on the current PR.
- `find-skills` — search for reusable Claude Code skills.
- `auto-skill-installer` — discover and install a skill from a natural-language request.

These skills are installed by `install-skills.sh` alongside the core autopilot skills.

## Before finishing any change

- Run the task's verification commands (or the detected lint/typecheck/test).
- Do not stop while verification is failing — the Stop hook will block.
- Inspect the diff.
- Record evidence under `.agent-runs/`.

## General conduct

- Treat the repository as the source of truth.
- Restate the goal before non-trivial work.
- Identify affected files before editing.
- Keep changes small, scoped, and verifiable.
- Preserve architecture boundaries and existing style.
- Validate external data at boundaries.
- Prefer shared utilities (`lib/agent_core.py`) over one-off helpers.
- Before finishing a change, run `/context` to surface files the change-graph flags as related (docs, tests, config that may need matching updates).
- Never hide failing tests or claim verification passed when it did not run.
