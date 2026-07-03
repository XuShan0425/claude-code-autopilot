---
description: Query the context graph for files related to your current or given changes
---

Query the project's change-graph (`.context-graph/`) for related files, then act
on the result.

```bash
python .claude/hooks/context_graph_hook.py query "$ARGUMENTS"
```

- No argument: uses the current working-tree changes (staged, unstaged, untracked).
- A file path argument: shows files the graph considers related to that file.

The graph records lightweight relations (for example, editing a file under
`src/commands/` or `commands/` flags `README.md` and `tests/`; editing config
flags docs and `.env.example`). Review every flagged file and update it if the
change actually affects it, then re-run verification. Do not blindly edit files
the graph lists — confirm the relation applies first.
