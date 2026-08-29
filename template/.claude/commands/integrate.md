---
description: List topic PRs that failed to auto-merge
---

Check for any standard topic branch PRs that did not auto-merge (optional EPIC filter:
$ARGUMENTS).

```bash
python orchestrator/agent-team.py integrate "$ARGUMENTS"
```

If there are stuck PRs, report each one with its number, title, and the retry
command. If branch protection is likely the cause, say so.
