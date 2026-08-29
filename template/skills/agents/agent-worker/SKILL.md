---
name: agent-worker
description: Use when executing a specific .agent-tasks task file. Implement only that task, stay within allowed files, run the task's verification, and leave the repo ready to merge. The orchestrator handles commit, push, PR, and auto-merge.
---

# Worker

Execute exactly one assigned task file. Do not expand scope.

## Workflow

1. Read `CLAUDE.md` and the assigned task file (`.agent-tasks/active/` or
   `running/`).
2. Restate: goal, allowed files, forbidden files, acceptance criteria,
   verification commands.
3. Inspect the relevant code; make a short plan.
4. Implement the smallest safe change. Add or update tests if behavior changes.
5. Run the task's verification commands (or the detected lint/typecheck/test).
6. Save notes in the task file or `docs/exec-plans/completed/`.
7. Leave the repository clean and ready for verification.

The orchestrator (or the Stop hook) then verifies, commits, pushes, opens a PR,
and auto-merges. You do not commit, push, or merge yourself when running under
the orchestrator.

## Scope discipline

Allowed: files listed under "Allowed files", plus tests/docs the task requires,
plus evidence under `.agent-runs/`.

Forbidden: everything else, including unrelated refactors, formatting, and
dependency changes. If the task requires touching a forbidden file, stop and
record the blocker in the task file.

If the task requires touching a secret-like path (`.env`, `secrets/`, names
containing `secret`/`token`), stop — those are blocked by the guardrail.

## Branch

Use the branch in the task file (`agent/...`). Never operate on `main` unless
the task explicitly sets it as the branch.

## Verification

Verification is the only quality gate before auto-merge. Run all commands
listed under "Verification commands". If they fail, fix the issue before
finishing — do not leave failing verification. Never hide or skip failing tests.

## Final response

End with: task completed or blocked · branch · verification result · risks.
