---
description: Plan from the main PRD, a feature brief, or a direct issue description via the orchestrator
---

Use the orchestrator to generate an EPIC plus bounded task files.

Routing rules:
- If the work changes product goals, target users, core flows, MVP scope, non-goals, or user-visible feature boundaries, plan from the **main PRD**.
- If the work is an additive/local product enhancement that should not rewrite the main PRD, plan from a **feature brief**.
- If the work is a bugfix, small repair, test gap, style fix, or localized optimization, it can be planned directly from the issue description.

Main PRD usage:

```bash
python orchestrator/agent-team.py plan --from-prd "$ARGUMENTS"
```

Feature brief usage:

```bash
python orchestrator/agent-team.py plan --from-brief "$ARGUMENTS"
```

Direct issue usage:

```bash
python orchestrator/agent-team.py plan "$ARGUMENTS"
```

If the request looks like a product-scope change but no PRD/brief is available, stop and tell the user to run `/prd` first.

Then run `python orchestrator/agent-team.py status` and summarize: the source used (main PRD, feature brief, or direct issue), the EPIC id, the task files created, their branches, and the recommended execution order.
Do not implement any task — planning only.
