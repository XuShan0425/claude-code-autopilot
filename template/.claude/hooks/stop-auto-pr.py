#!/usr/bin/env python3
"""Claude Code Stop hook (autopilot mode).

On each stop, when there are meaningful changes, this hook:
  1. refuses secret / .env paths — the one guardrail kept in autopilot mode;
  2. runs detected verification, the sole quality gate — blocks (exit 2) on
     failure so Claude keeps iterating until it passes;
  3. commits, pushes, and (on a feature branch) opens a PR and auto-merges it,
     then returns the checkout to the default branch.

It steps aside (exit 0) inside an orchestrator worker session
(``AGENT_TEAM_WORKER`` is set), so the orchestrator owns post-processing there.

Protocol: block = exit 2 with stderr (fed back to Claude); allow = exit 0,
optionally with a ``systemMessage`` JSON payload for the user.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# Bootstrap: resolve agent_core at <repo>/lib/ (parents[2] = repo root).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import agent_core as core  # noqa: E402


# --------------------------------------------------------------------------- #
# Protocol helpers
# --------------------------------------------------------------------------- #

def debug(message: str) -> None:
    sys.stderr.write(f"[stop-auto-pr] {message}\n")
    sys.stderr.flush()


def done(message: str | None = None) -> None:
    """Allow Claude to stop. Optionally surface a message to the user."""
    if message:
        sys.stdout.write(json.dumps({"systemMessage": message}, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    sys.exit(0)


def block(reason: str) -> None:
    """Prevent Claude from stopping; ``reason`` is fed back as its next task."""
    sys.stderr.write(reason + "\n")
    sys.stderr.flush()
    sys.exit(2)


# --------------------------------------------------------------------------- #
# Git state inspection (hook-specific)
# --------------------------------------------------------------------------- #

def split_nul(value: str) -> list[str]:
    return [item for item in value.split("\0") if item]


def unique_paths(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out


def staged_paths(repo: Path) -> list[str]:
    result = core.run(["git", "diff", "--cached", "--name-only", "-z"], repo, check=True)
    return split_nul(result.stdout)


def unstaged_paths(repo: Path) -> list[str]:
    result = core.run(["git", "ls-files", "-m", "-d", "-o", "--exclude-standard", "-z"], repo, check=True)
    return split_nul(result.stdout)


def unmerged_paths(repo: Path) -> list[str]:
    result = core.run(["git", "diff", "--name-only", "--diff-filter=U", "-z"], repo, check=True)
    return split_nul(result.stdout)


def git_path(repo: Path, ref: str) -> Path:
    result = core.run(["git", "rev-parse", "--git-path", ref], repo, check=True)
    path = Path(result.stdout.strip())
    return path if path.is_absolute() else repo / path


def in_progress_git_operations(repo: Path) -> list[str]:
    ops: list[str] = []
    if git_path(repo, "rebase-apply").exists() or git_path(repo, "rebase-merge").exists():
        ops.append("rebase")
    if git_path(repo, "MERGE_HEAD").exists():
        ops.append("merge")
    if git_path(repo, "CHERRY_PICK_HEAD").exists():
        ops.append("cherry-pick")
    if git_path(repo, "REVERT_HEAD").exists():
        ops.append("revert")
    return ops


def visible_changed_paths(repo: Path) -> list[str]:
    return [
        path
        for path in unique_paths([*staged_paths(repo), *unstaged_paths(repo)])
        if not core.is_runtime_artifact(path)
    ]


def commit(repo: Path, branch: str, log_path: Path) -> str | None:
    if core.run(["git", "diff", "--cached", "--quiet"], repo).returncode == 0:
        return None
    subject = "chore: agent auto-merge"
    paths = staged_paths(repo)
    listed = "\n".join(f"- {path}" for path in paths[:80])
    if len(paths) > 80:
        listed += f"\n- ... and {len(paths) - 80} more"
    body = f"Automated Stop-hook commit.\n\nBranch: {branch}\n\nFiles:\n{listed}\n"
    core.run(["git", "commit", "-m", subject, "-m", body], repo, check=True)
    sha = core.git_stdout(repo, ["rev-parse", "--short", "HEAD"], check=True)
    core.log_event(log_path, "commit_created", branch=branch, commit=sha, paths=paths)
    return sha


# --------------------------------------------------------------------------- #
# Main flow
# --------------------------------------------------------------------------- #

def main() -> None:
    # Orchestrator worker sessions manage their own commit/push/merge.
    if os.environ.get("AGENT_TEAM_WORKER"):
        done()

    # Drain stdin (hook payload) for logging; we don't gate on it.
    try:
        raw = sys.stdin.read()
        hook_input: Any = json.loads(raw) if raw.strip() else {}
        if not isinstance(hook_input, dict):
            hook_input = {}
    except json.JSONDecodeError:
        hook_input = {}

    try:
        repo = core.repo_root(Path.cwd())
    except core.AgentError:
        done("no Git repository; Stop hook skipped")

    debug(f"repo: {repo}")

    ops = in_progress_git_operations(repo)
    if ops:
        block("Git is mid-operation (" + ", ".join(ops) + "); resolve that state before finishing.")

    conflicts = unmerged_paths(repo)
    if conflicts:
        block("Resolve Git conflict markers before finishing:\n" + "\n".join(f"- {p}" for p in conflicts))

    changes = visible_changed_paths(repo)
    if not changes:
        done()

    log_path = repo / ".agent-runs" / f"stop-{core.utc_stamp()}.jsonl"
    core.log_event(log_path, "hook_start", changes=changes)

    # 1. Secret guardrail — the one rail kept in autopilot mode.
    secret_paths = [path for path in changes if core.is_sensitive_path(path)]
    if secret_paths:
        reason = (
            "Refusing to commit files matching secret patterns:\n"
            + "\n".join(f"- {path}" for path in secret_paths)
            + "\nRemove them and stop again."
        )
        core.log_event(log_path, "blocked_secret", paths=secret_paths)
        block(reason)

    # 2. Verification — the sole quality gate.
    commands = core.detect_verification_commands(repo)
    results, ok = core.run_verification(repo, commands, log_path)
    if not ok:
        failed = next((r for r in results if r.returncode != 0), None)
        detail = ""
        if failed:
            detail = (
                f"\n\n{core.display_cmd(failed.command.args)}\n"
                f"stdout:\n{core.tail(failed.stdout)}\n"
                f"stderr:\n{core.tail(failed.stderr)}"
            )
        core.log_event(log_path, "verification_failed")
        block("Verification failed. Fix it before stopping again." + detail)

    # 3. Commit, push, and auto-merge (feature branch) or push (default branch).
    default_branch = core.detect_default_branch(repo)
    branch = core.current_branch(repo)
    core.log_event(log_path, "changes_verified", branch=branch, default_branch=default_branch)

    core.ensure_origin(repo)
    core.run(["git", "add", "-A"], repo, check=True)
    sha = commit(repo, branch, log_path)
    if sha is None:
        done("nothing to commit after staging")

    core.push_branch(repo, branch)

    if branch == default_branch or branch == "HEAD":
        core.log_event(log_path, "completed", branch=branch, commit=sha, merged=False)
        done(f"verified, committed {sha}, and pushed to `{branch}`")

    report = core.verification_report(results, commands)
    body = (
        "Automated agent PR (Stop hook, autopilot mode).\n\n"
        f"- Branch: `{branch}`\n- Base: `{default_branch}`\n- Commit: `{sha}`\n\n"
        f"Verification:\n{report}\n\n"
        "Auto-merged; no human review required.\n"
    )
    pr_url = core.open_or_update_pr(repo, branch, default_branch, f"Agent: {branch}", body)
    core.merge_pr(repo, pr_url)

    # Return the checkout to the default branch, up to date.
    core.run(["git", "switch", default_branch], repo, check=True)
    core.run(["git", "pull", "--ff-only", f"origin/{default_branch}"], repo)
    if core.branch_exists(repo, branch):
        core.run(["git", "branch", "-D", branch], repo)

    core.log_event(log_path, "completed", branch=branch, commit=sha, merged=True, pr=pr_url)
    done(f"verified, committed {sha}, and auto-merged `{branch}` -> `{default_branch}`: {pr_url}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except core.AgentError as exc:
        sys.stderr.write(f"Stop hook error: {exc}\n")
        sys.exit(2)
    except Exception as exc:
        sys.stderr.write(f"Stop hook unexpected error: {exc!r}\n")
        sys.exit(2)
