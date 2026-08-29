---
description: Run a task in an isolated worktree, verify, and auto-merge
---

Run task `$ARGUMENTS` through the orchestrator (autopilot). It will implement
the task in a worktree, verify, commit, push, open a PR, and auto-merge it.

```bash
python orchestrator/agent-team.py run "$ARGUMENTS"
```

Report the outcome: branch (merged), commit, PR URL, and the task's final state.
If it failed, read the run log under `.agent-runs/`, explain the failure, and
propose the fix.
