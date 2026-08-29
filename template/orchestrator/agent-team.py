#!/usr/bin/env python3
"""Agent task orchestrator.

Drives the plan -> run -> status flow on top of :mod:`agent_core`.

    python orchestrator/agent-team.py plan  --from-prd docs/prd/active/PRD-001.md
    python orchestrator/agent-team.py plan  "fix login button not responding"
    python orchestrator/agent-team.py run   TASK-001
    python orchestrator/agent-team.py status
    python orchestrator/agent-team.py integrate [EPIC-XXX]

In autopilot mode ``run`` executes a task in an isolated worktree via a
headless Claude Code session, verifies, commits, pushes, opens a PR, then
auto-merges it. The task ends in ``completed``.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

# Bootstrap: let ``import agent_core`` resolve to template/lib/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import agent_core as core  # noqa: E402


TASK_STATES = ("active", "running", "pr-opened", "completed", "failed")
PRD_ID_PATTERN = re.compile(r"^PRD-[A-Za-z0-9._-]+$", re.IGNORECASE)
BRIEF_ID_PATTERN = re.compile(r"^FEATURE-[A-Za-z0-9._-]+$", re.IGNORECASE)

ISSUE_HINTS = (
    "bug",
    "debug",
    "fix",
    "broken",
    "error",
    "exception",
    "failing",
    "failure",
    "regression",
    "optimize",
    "optimization",
    "performance",
    "cleanup",
    "refactor",
    "logging",
    "log",
    "style",
    "css",
    "copy",
    "typo",
    "test",
    "修复",
    "报错",
    "错误",
    "异常",
    "崩溃",
    "无响应",
    "优化",
    "性能",
    "重构",
    "补测试",
    "样式",
    "文案",
)

PRODUCT_HINTS = (
    "new feature",
    "feature",
    "add",
    "build",
    "create",
    "support",
    "product",
    "workflow",
    "module",
    "page",
    "flow",
    "onboarding",
    "subscription",
    "team",
    "invite",
    "dashboard",
    "checkout",
    "role",
    "permission",
    "新增",
    "增加",
    "做一个",
    "功能",
    "模块",
    "页面",
    "流程",
    "产品",
    "引导",
    "订阅",
    "协作",
    "邀请",
    "权限",
    "支持",
    "改版",
    "重做",
)


# --------------------------------------------------------------------------- #
# Headless Claude execution
# --------------------------------------------------------------------------- #

def claude_exec(cwd: Path, prompt: str, log_path: Path) -> int:
    """Run a headless Claude Code session (full autonomy, no prompts).

    Sets ``AGENT_TEAM_WORKER`` so the project Stop hook steps aside — the
    orchestrator owns verification / commit / push / merge for this session.
    """
    if shutil.which("claude") is None:
        raise core.AgentError("claude CLI was not found in PATH")
    cmd = ["claude", "-p", prompt, "--dangerously-skip-permissions"]
    env = {**os.environ, "AGENT_TEAM_WORKER": "1"}
    core.log_event(log_path, "claude_start", command=cmd)
    completed = subprocess.run(cmd, cwd=str(cwd), env=env, text=True, capture_output=True)
    core.log_event(log_path, "claude_stdout", text=core.tail(completed.stdout))
    if completed.stderr:
        core.log_event(log_path, "claude_stderr", text=core.tail(completed.stderr))
    core.log_event(log_path, "claude_finish", returncode=completed.returncode)
    return completed.returncode


# --------------------------------------------------------------------------- #
# Filesystem / task state
# --------------------------------------------------------------------------- #

def ensure_dirs(root: Path) -> None:
    for rel in (
        ".agent-runs",
        ".agent-tasks/active",
        ".agent-tasks/running",
        ".agent-tasks/pr-opened",
        ".agent-tasks/completed",
        ".agent-tasks/failed",
        "docs/prd/active",
        "docs/prd/completed",
        "docs/prd/changes/active",
        "docs/prd/changes/completed",
        "docs/exec-plans/active",
        "docs/exec-plans/completed",
    ):
        (root / rel).mkdir(parents=True, exist_ok=True)


def move_task(root: Path, source: Path, state: str, note: str | None = None) -> Path:
    destination = root / ".agent-tasks" / state / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = source.read_text(encoding="utf-8")
    if note:
        text += f"\n\n## Orchestrator Note\n\n{note}\n"
    destination.write_text(text, encoding="utf-8")
    if source.resolve() != destination.resolve():
        source.unlink()
    return destination


def find_task(root: Path, task_id: str) -> Path:
    names = [task_id, f"{task_id}.md"] if not task_id.endswith(".md") else [task_id]
    for state in ("active", "running", "failed"):
        for name in names:
            path = root / ".agent-tasks" / state / name
            if path.exists():
                return path
    raise core.AgentError(f"task not found in active/running/failed: {task_id}")


def relpath_text(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def write_summary(path: Path, title: str, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join([f"# {title}", "", *lines, ""]), encoding="utf-8")


def list_active_prds(root: Path) -> list[Path]:
    prd_dir = root / "docs" / "prd" / "active"
    if not prd_dir.exists():
        return []
    return sorted(
        (path for path in prd_dir.glob("*.md") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def resolve_main_prd(root: Path) -> Path | None:
    active = list_active_prds(root)
    if not active:
        return None
    explicit_main = [path for path in active if path.name.upper() == "PRD-001.MD"]
    return explicit_main[0] if explicit_main else active[0]


def list_active_feature_briefs(root: Path) -> list[Path]:
    brief_dir = root / "docs" / "prd" / "changes" / "active"
    if not brief_dir.exists():
        return []
    return sorted(
        (path for path in brief_dir.glob("*.md") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def resolve_latest_prd(root: Path) -> Path | None:
    return resolve_main_prd(root)


def resolve_prd_reference(root: Path, value: str | None) -> Path | None:
    if not value:
        return None

    candidate = Path(value)
    if candidate.is_absolute() and candidate.exists():
        return candidate.resolve()

    repo_candidate = (root / value).resolve()
    if repo_candidate.exists():
        return repo_candidate

    if PRD_ID_PATTERN.match(value):
        for state in ("active", "completed"):
            prd_candidate = root / "docs" / "prd" / state / f"{value}.md"
            if prd_candidate.exists():
                return prd_candidate.resolve()
    return None


def resolve_feature_brief_reference(root: Path, value: str | None) -> Path | None:
    if not value:
        return None

    candidate = Path(value)
    if candidate.is_absolute() and candidate.exists():
        return candidate.resolve()

    repo_candidate = (root / value).resolve()
    if repo_candidate.exists():
        return repo_candidate

    if BRIEF_ID_PATTERN.match(value):
        for state in ("active", "completed"):
            brief_candidate = root / "docs" / "prd" / "changes" / state / f"{value}.md"
            if brief_candidate.exists():
                return brief_candidate.resolve()
    return None


def resolve_feature_brief(root: Path, value: str | None) -> Path | None:
    return resolve_feature_brief_reference(root, value)


def classify_plan_input(text: str | None) -> str:
    normalized = (text or "").strip().lower()
    if not normalized:
        return "requires_prd"

    has_issue_hint = any(hint in normalized for hint in ISSUE_HINTS)
    has_product_hint = any(hint in normalized for hint in PRODUCT_HINTS)

    if has_issue_hint and not has_product_hint:
        return "direct_issue"
    if has_product_hint:
        return "requires_prd"
    return "requires_prd"


def classify_prd_request(text: str | None, has_main_prd: bool) -> str:
    normalized = (text or "").strip().lower()
    if not normalized:
        return "main_prd"

    issue_markers = (
        "bug", "fix", "debug", "error", "exception", "optimize", "performance",
        "修复", "报错", "错误", "异常", "优化", "性能", "重构", "补测试", "样式", "文案",
    )
    first_principles_markers = (
        "goal", "vision", "persona", "user", "mvp", "non-goal", "scope", "core flow",
        "目标", "用户", "核心流程", "mvp", "非目标", "范围", "定位", "第一性原则", "产品目标",
    )
    feature_markers = (
        "feature", "invite", "team", "export", "dashboard", "module", "flow", "page",
        "功能", "模块", "邀请", "团队", "导出", "页面", "流程", "新增", "增加", "支持",
    )

    if any(marker in normalized for marker in issue_markers):
        return "direct_issue"
    if not has_main_prd:
        return "main_prd"
    if any(marker in normalized for marker in first_principles_markers):
        return "main_prd"
    if any(marker in normalized for marker in feature_markers):
        return "feature_brief"
    return "main_prd"


def build_main_prd_plan_prompt(prd_text: str, prd_path: str, epic_id: str) -> str:
    return f"""
You are in planner mode. Do not implement business logic.

You must read the main PRD below and use it as the single source of truth for first-principles product definition.

Main PRD path:
{prd_path}

Main PRD contents:
{prd_text}

Rules:
- Do not do product discovery here; the main PRD already defines the product intent.
- Do not invent product requirements that are not supported by the main PRD.
- If the main PRD has unresolved or ambiguous items, record them as assumptions or open questions in the EPIC instead of making them up.
- If chat context and the main PRD conflict, follow the main PRD.
- Create a concise execution plan at docs/exec-plans/active/{epic_id}.md.
- Create one or more task files under .agent-tasks/active/ using TASK-001.md, TASK-002.md, and so on.
- Use .agent-tasks/active/TASK-template.md as the task format.
- The EPIC must include Requirements Source `{prd_path}`, Parent PRD `{prd_path}`, Parent Brief `N/A`, and Work Type `product`.
- Every task must set Work Type to `product`, Requirements Source to `{prd_path}`, Parent PRD to `{prd_path}`, and Parent Brief to `N/A`.
- Each task must include parent epic, goal, scope, allowed files, forbidden files, acceptance criteria, verification commands, branch, base branch, and output requirements.
- Use standard topic branch names: `feature/<name>`, `fix/<name>`, `refactor/<name>`, or `chore/<name>`.
- Keep tasks small enough to run independently in a worktree.
- If a task touches lockfiles, schema, shared types, or auth boundaries, call out that it is unsafe for parallel execution.
- Do not edit application code.
""".strip()


def build_prd_plan_prompt(prd_text: str, prd_path: str, epic_id: str) -> str:
    return build_main_prd_plan_prompt(prd_text, prd_path, epic_id)


def build_feature_brief_plan_prompt(main_prd_text: str, main_prd_path: str, brief_text: str, brief_path: str, epic_id: str) -> str:
    return f"""
You are in planner mode. Do not implement business logic.

You must read both the main PRD and the feature brief below.
The main PRD defines the first-principles product direction and remains the single source of truth.
The feature brief is an additive local delta whose parent PRD remains `{main_prd_path}`.

Main PRD path:
{main_prd_path}

Main PRD contents:
{main_prd_text}

Feature brief path:
{brief_path}

Feature brief contents:
{brief_text}

Rules:
- Treat the main PRD as the primary product-definition source.
- Treat the feature brief as an additive delta, not a replacement for the main PRD.
- Do not invent product requirements beyond the main PRD + brief.
- If the brief conflicts with the main PRD, call out the conflict in the EPIC instead of silently choosing one.
- Create a concise execution plan at docs/exec-plans/active/{epic_id}.md.
- Create one or more task files under .agent-tasks/active/ using TASK-001.md, TASK-002.md, and so on.
- Use .agent-tasks/active/TASK-template.md as the task format.
- The EPIC must include Requirements Source `{brief_path}`, Parent PRD `{main_prd_path}`, Parent Brief `{brief_path}`, and Work Type `feature`.
- Every task must set Work Type to `feature`, Requirements Source to `{brief_path}`, Parent PRD to `{main_prd_path}`, and Parent Brief to `{brief_path}`.
- Each task must include parent epic, goal, scope, allowed files, forbidden files, acceptance criteria, verification commands, branch, base branch, and output requirements.
- Use standard topic branch names: `feature/<name>`, `fix/<name>`, `refactor/<name>`, or `chore/<name>`.
- Keep tasks small enough to run independently in a worktree.
- If a task touches lockfiles, schema, shared types, or auth boundaries, call out that it is unsafe for parallel execution.
- Do not edit application code.
""".strip()


def build_issue_plan_prompt(requirement: str, epic_id: str) -> str:
    return f"""
You are in planner mode for a focused engineering issue, bugfix, or small scoped change.
Do not implement business logic.

Issue / change request:
{requirement}

Rules:
- Treat this as a direct planning request only if it is a bugfix, small repair, localized optimization, test gap, or other engineering-scoped change.
- Do not broaden the scope into a product redesign or new feature proposal.
- If this issue actually changes product goals, target users, core flows, MVP scope, non-goals, or user-visible feature boundaries, stop and say a PRD is required first.
- No PRD or feature brief is required for this route unless the scope expands into product-definition work.
- Create a concise execution plan at docs/exec-plans/active/{epic_id}.md.
- Create one or more task files under .agent-tasks/active/ using TASK-001.md, TASK-002.md, and so on.
- Use .agent-tasks/active/TASK-template.md as the task format.
- The EPIC must include Requirements Source `direct request`, Parent PRD `N/A`, Parent Brief `N/A`, and Work Type `issue`.
- Every task must set Work Type to `issue`, Requirements Source to `direct request`, Parent PRD to `N/A`, and Parent Brief to `N/A` unless an explicit product document is later supplied.
- Each task must include parent epic, goal, scope, allowed files, forbidden files, acceptance criteria, verification commands, branch, base branch, and output requirements.
- Use standard topic branch names: `feature/<name>`, `fix/<name>`, `refactor/<name>`, or `chore/<name>`.
- Keep tasks narrow, verifiable, and focused on restoring or improving the described behavior.
- Do not edit application code.
""".strip()


def find_active_tasks_by_brief(root: Path, brief_relpath: str) -> list[Path]:
    matches: list[Path] = []
    target = brief_relpath.replace("\\", "/")
    for state in ("active", "running", "pr-opened", "failed"):
        state_dir = root / ".agent-tasks" / state
        if not state_dir.exists():
            continue
        for path in state_dir.glob("*.md"):
            if path.name == "TASK-template.md":
                continue
            text = path.read_text(encoding="utf-8")
            parent_brief = parse_markdown_field(text, "Brief")
            if parent_brief and parent_brief.replace("\\", "/") == target:
                matches.append(path)
    return matches


def archive_feature_brief_if_complete(root: Path, brief_path: Path, note: str | None = None) -> Path | None:
    if not brief_path.exists():
        return None

    changes_root = (root / "docs" / "prd" / "changes").resolve()
    try:
        brief_path.resolve().relative_to(changes_root)
    except ValueError:
        return None

    brief_relpath = relpath_text(brief_path, root)
    if find_active_tasks_by_brief(root, brief_relpath):
        return None

    completed_dir = root / "docs" / "prd" / "changes" / "completed"
    completed_dir.mkdir(parents=True, exist_ok=True)
    destination = completed_dir / brief_path.name
    text = brief_path.read_text(encoding="utf-8")
    if note:
        text += f"\n\n## Archive Note\n\n{note}\n"
    destination.write_text(text, encoding="utf-8")
    if destination.resolve() != brief_path.resolve():
        brief_path.unlink()
    return destination


def parse_task_metadata(text: str) -> dict[str, str | None]:
    return {
        "work_type": parse_markdown_field(text, "Work type"),
        "requirements_source": parse_markdown_field(text, "Source"),
        "parent_prd": parse_markdown_field(text, "PRD"),
        "parent_brief": parse_markdown_field(text, "Brief"),
        "parent_epic": parse_markdown_field(text, "Epic"),
        "branch": parse_markdown_field(text, "Branch"),
        "base_branch": parse_markdown_field(text, "Base branch"),
    }


def validate_task_traceability(text: str) -> dict[str, str | None]:
    metadata = parse_task_metadata(text)
    errors: list[str] = []

    work_type = metadata["work_type"]
    requirements_source = metadata["requirements_source"]
    parent_prd = metadata["parent_prd"]
    parent_brief = metadata["parent_brief"]
    parent_epic = metadata["parent_epic"]
    branch = metadata["branch"]
    base_branch = metadata["base_branch"]

    if not branch:
        errors.append("Missing required field: Branch")
    if not base_branch:
        errors.append("Missing required field: Base branch")
    if not work_type:
        errors.append("Missing required field: Work Type")
    elif work_type not in {"product", "feature", "issue"}:
        errors.append(f"Unsupported Work Type: {work_type}")
    if not requirements_source:
        errors.append("Missing required field: Requirements Source")
    if not parent_epic:
        errors.append("Missing required field: Parent Epic")

    if work_type == "product":
        if not parent_prd:
            errors.append("Product tasks must set Parent PRD")
        if parent_brief:
            errors.append("Product tasks must not set Parent Brief")
    elif work_type == "feature":
        if not parent_prd:
            errors.append("Feature tasks must set Parent PRD")
        if not parent_brief:
            errors.append("Feature tasks must set Parent Brief")
    elif work_type == "issue":
        if parent_prd:
            errors.append("Issue tasks must not set Parent PRD")
        if parent_brief:
            errors.append("Issue tasks must not set Parent Brief")

    if errors:
        raise core.AgentError("Task traceability validation failed:\n- " + "\n- ".join(errors))
    return metadata


# --------------------------------------------------------------------------- #
# Worktree isolation
# --------------------------------------------------------------------------- #

def worktree_path(root: Path, task_id: str) -> Path:
    return root.parent / f"{root.name}.agent-worktrees" / core.slug(task_id)


def worktree_inventory(root: Path) -> dict[Path, str | None]:
    result = core.run(["git", "worktree", "list", "--porcelain"], root, check=True)
    inventory: dict[Path, str | None] = {}
    current_path: Path | None = None
    current_branch: str | None = None

    for line in result.stdout.splitlines():
        if not line:
            if current_path is not None:
                inventory[current_path] = current_branch
            current_path = None
            current_branch = None
            continue
        if line.startswith("worktree "):
            current_path = Path(line.split(" ", 1)[1]).resolve()
        elif line.startswith("branch "):
            ref = line.split(" ", 1)[1].strip()
            current_branch = ref.removeprefix("refs/heads/") if ref.startswith("refs/heads/") else ref

    if current_path is not None:
        inventory[current_path] = current_branch
    return inventory


def worktree_branch_path(root: Path, branch: str) -> Path | None:
    for path, current_branch in worktree_inventory(root).items():
        if current_branch == branch:
            return path
    return None


def task_branch(metadata: dict[str, str | None], task_id: str) -> str:
    """Resolve a task branch using the repository's public naming convention."""
    explicit = (metadata.get("branch") or "").strip()
    if explicit:
        core.ensure_standard_topic_branch(explicit)
        return explicit
    work_type = (metadata.get("work_type") or "issue").lower()
    prefix = {
        "product": "feature/",
        "feature": "feature/",
        "issue": "fix/",
    }.get(work_type, "chore/")
    return prefix + core.slug(task_id)


def ensure_worktree(root: Path, path: Path, branch: str, base_branch: str) -> None:
    core.ensure_standard_topic_branch(branch)
    if core.is_protected_branch(base_branch):
        base_ref = f"origin/{base_branch}"
    else:
        base_ref = f"origin/{base_branch}" if core.remote_ref_exists(root, base_branch) else base_branch
    if not core.ref_exists(root, base_ref):
        raise core.AgentError(f"Base branch does not exist: {base_branch}")
    path.parent.mkdir(parents=True, exist_ok=True)
    inventory = worktree_inventory(root)
    resolved_path = path.resolve()

    if resolved_path in inventory:
        current_branch = inventory[resolved_path]
        if current_branch != branch:
            raise core.AgentError(
                f"worktree path already exists for branch {current_branch or 'detached'}: {path}"
            )
        return

    if path.exists():
        raise core.AgentError(f"worktree path already exists on disk but is not registered with git: {path}")

    occupied = worktree_branch_path(root, branch)
    if occupied is not None:
        raise core.AgentError(
            f"branch {branch} is already checked out in worktree {occupied}; refusing to reuse it"
        )

    if core.branch_exists(root, branch):
        core.run(["git", "worktree", "add", str(path), branch], root, check=True)
    else:
        core.run(["git", "worktree", "add", "-b", branch, str(path), base_ref], root, check=True)


def commit_all(root: Path, message: str) -> str | None:
    core.validate_conventional_commit_subject(message)
    core.ensure_no_sensitive_changes(root)
    paths = [path for path in core.changed_paths(root) if not core.is_runtime_artifact(path)]
    if not paths:
        return None
    core.run(["git", "add", "-A", "--", *paths], root, check=True)
    staged = core.git_stdout(root, ["diff", "--cached", "--name-only"], check=True)
    staged_paths = [path for path in staged.splitlines() if path]
    sensitive_staged = [path for path in staged_paths if core.is_sensitive_path(path)]
    if sensitive_staged:
        raise core.AgentError(
            "Refusing to commit sensitive staged paths:\n"
            + "\n".join(f"- {path}" for path in sensitive_staged)
        )
    if core.run(["git", "diff", "--cached", "--quiet"], root).returncode == 0:
        return None
    core.run(["git", "commit", "-m", message], root, check=True)
    return core.git_stdout(root, ["rev-parse", "--short", "HEAD"], check=True)


# --------------------------------------------------------------------------- #
# Markdown task parsing
# --------------------------------------------------------------------------- #

def parse_markdown_field(text: str, name: str) -> str | None:
    pattern = re.compile(rf"^\s*[-*]?\s*{re.escape(name)}\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
    match = pattern.search(text)
    if not match:
        return None
    value = match.group(1).strip().strip("`").strip("'\"").strip()
    return None if value.upper() in {"TBD", "N/A", "NONE"} else value


def parse_verification_from_task(text: str) -> list[core.VerifyCommand]:
    commands: list[core.VerifyCommand] = []
    in_section = False
    for line in text.splitlines():
        if re.match(r"^#{1,6}\s+verification commands\s*$", line.strip(), re.IGNORECASE):
            in_section = True
            continue
        if in_section and line.startswith("#"):
            break
        if not in_section:
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("<!--"):
            continue
        if stripped.startswith("-"):
            candidate = stripped[1:].strip()
            if candidate.startswith("`") and candidate.endswith("`"):
                candidate = candidate[1:-1]
            if candidate and candidate.upper() not in {"TBD", "NONE", "N/A"}:
                commands.append(core.VerifyCommand(f"task:{len(commands) + 1}", shlex.split(candidate)))
    return commands


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def command_plan(args: argparse.Namespace) -> int:
    root = core.repo_root(Path.cwd())
    ensure_dirs(root)
    epic_id = f"EPIC-{core.utc_stamp()}"
    run_id = f"{core.utc_stamp()}-plan"
    log_path = root / ".agent-runs" / f"{run_id}.jsonl"
    summary_path = root / ".agent-runs" / f"{run_id}-summary.md"

    prd_path = resolve_prd_reference(root, args.from_prd)
    brief_path = resolve_feature_brief(root, args.from_brief)
    requirement = (args.requirement or "").strip()

    if brief_path is None and requirement:
        brief_path = resolve_feature_brief(root, requirement)
    if prd_path is None and requirement:
        prd_path = resolve_prd_reference(root, requirement)

    if brief_path is not None:
        main_prd = resolve_main_prd(root)
        if main_prd is None:
            raise core.AgentError(
                "A feature brief was supplied, but no active main PRD exists. Create the main PRD first via /prd before planning from a feature brief."
            )
        main_relpath = relpath_text(main_prd, root)
        brief_relpath = relpath_text(brief_path, root)
        prompt = build_feature_brief_plan_prompt(
            main_prd.read_text(encoding="utf-8"),
            main_relpath,
            brief_path.read_text(encoding="utf-8"),
            brief_relpath,
            epic_id,
        )
        source_label = f"feature brief: `{brief_relpath}` (main PRD: `{main_relpath}`)"
    elif prd_path is not None:
        prd_relpath = relpath_text(prd_path, root)
        prompt = build_main_prd_plan_prompt(prd_path.read_text(encoding="utf-8"), prd_relpath, epic_id)
        source_label = f"main PRD: `{prd_relpath}`"
    else:
        if not requirement:
            prd_path = resolve_latest_prd(root)
            if prd_path is not None:
                prd_relpath = relpath_text(prd_path, root)
                prompt = build_main_prd_plan_prompt(prd_path.read_text(encoding="utf-8"), prd_relpath, epic_id)
                source_label = f"main PRD: `{prd_relpath}`"
            else:
                raise core.AgentError(
                    "No product document or issue description was supplied. For product-scope work, run /prd first or pass --from-prd/--from-brief. For bugfixes, pass the issue description directly."
                )
        else:
            route = classify_plan_input(requirement)
            if route != "direct_issue":
                raise core.AgentError(
                    "This request looks like a product-scope change. Create or update the main PRD or a feature brief via /prd, then rerun plan with --from-prd <path> or --from-brief <path>."
                )
            prompt = build_issue_plan_prompt(requirement, epic_id)
            source_label = f"Issue: `{requirement}`"

    rc = claude_exec(root, prompt, log_path)
    write_summary(
        summary_path,
        f"{epic_id} Plan",
        [
            f"- Source: {source_label}",
            f"- Log: `{log_path.relative_to(root)}`",
            f"- Exit code: `{rc}`",
        ],
    )
    print(f"planner exit code: {rc}")
    print(f"source: {source_label}")
    print(f"log: {log_path.relative_to(root)}")
    return rc


def command_run(args: argparse.Namespace) -> int:
    root = core.repo_root(Path.cwd())
    ensure_dirs(root)
    task_path = find_task(root, args.task)
    task_id = task_path.stem
    task_text = task_path.read_text(encoding="utf-8")
    metadata = validate_task_traceability(task_text)
    default_branch = core.detect_default_branch(root)
    branch = task_branch(metadata, task_id)
    base_branch = metadata["base_branch"] or default_branch

    run_id = f"{core.utc_stamp()}-{task_id}"
    running_task = move_task(root, task_path, "running", f"Started at {core.utc_stamp()} on branch `{branch}`.")
    wt_path = worktree_path(root, task_id)
    # Default to root-level artifacts; reassigned to worktree-level once it exists.
    log_path = root / ".agent-runs" / f"{run_id}.jsonl"
    summary_path = root / ".agent-runs" / f"{run_id}-summary.md"

    try:
        core.fetch_base_branch(root, base_branch)
        ensure_worktree(root, wt_path, branch, base_branch)
        ensure_dirs(wt_path)
        log_path = wt_path / ".agent-runs" / f"{run_id}.jsonl"
        summary_path = wt_path / ".agent-runs" / f"{run_id}-summary.md"
        wt_task = wt_path / ".agent-tasks" / "running" / running_task.name
        wt_task.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(running_task, wt_task)

        prompt = f"""
You are a Claude Code worker running non-interactively in a task worktree.

Execute exactly this task:

{task_text}

Rules:
- Stay on branch {branch}.
- Modify only files allowed by the task.
- Do not merge branches or open a PR; the orchestrator verifies, commits, pushes, opens a PR, and auto-merges after verification passes.
- Save durable notes in the task file or docs/exec-plans/completed/.
- Leave the repository ready for verification.
"""
        rc = claude_exec(wt_path, prompt.strip(), log_path)
        if rc != 0:
            write_summary(summary_path, f"{task_id} Failed", [f"- Claude exit code: `{rc}`", f"- Log: `{relpath_text(log_path, wt_path)}`"])
            move_task(root, running_task, "failed", f"Claude exited with code {rc}. Log: `{log_path}`.")
            print(f"task failed: {task_id}\nlog: {log_path}")
            return rc

        commands = parse_verification_from_task(task_text) or core.detect_verification_commands(wt_path)
        results, ok = core.run_verification(wt_path, commands, log_path)
        if not ok:
            failed = next((r for r in results if r.returncode != 0), None)
            detail = ""
            if failed:
                detail = f"\n\n{core.display_cmd(failed.command.args)}\nstdout:\n{core.tail(failed.stdout)}\nstderr:\n{core.tail(failed.stderr)}"
            write_summary(summary_path, f"{task_id} Failed Verification", [f"- Log: `{relpath_text(log_path, wt_path)}`"])
            move_task(root, running_task, "failed", f"Verification failed.{detail} Log: `{log_path}`.")
            print(f"verification failed for {task_id}\nlog: {log_path}")
            return 1

        # Sync with the latest base immediately before creating the atomic commit.
        core.fetch_base_branch(wt_path, base_branch)
        core.rebase_onto_base(wt_path, base_branch)
        results, ok = core.run_verification(wt_path, commands, log_path)
        if not ok:
            write_summary(summary_path, f"{task_id} Failed Verification After Rebase", [f"- Log: `{relpath_text(log_path, wt_path)}`"])
            move_task(root, running_task, "failed", f"Verification failed after rebase. Log: `{log_path}`.")
            print(f"verification failed after rebase for {task_id}\nlog: {log_path}")
            return 1

        core.ensure_origin(wt_path)
        core.ensure_gh_auth(wt_path)

        commit_type = {"product": "feat", "feature": "feat", "issue": "fix"}.get(metadata["work_type"] or "issue", "chore")
        commit_subject = core.conventional_commit_subject(commit_type, f"complete {task_id}")
        commit_sha = commit_all(wt_path, commit_subject)
        if commit_sha is None:
            write_summary(summary_path, f"{task_id} Failed", [f"- Reason: `No changes were produced`", f"- Log: `{relpath_text(log_path, wt_path)}`"])
            move_task(root, running_task, "failed", "No changes were produced, so no PR was opened.")
            print(f"no changes to commit for {task_id}")
            return 1

        core.push_branch(wt_path, branch)
        report = core.verification_report(results, commands)
        body = (
            f"Task: `{task_id}`\n\n"
            f"Branch: `{branch}`\nBase: `{base_branch}`\nCommit: `{commit_sha}`\n"
            f"Run log: `{log_path.relative_to(wt_path)}`\n\n"
            f"Verification:\n{report}\n\n"
            "Opened and auto-merged by the agent orchestrator (autopilot mode).\n"
        )
        pr_url = core.open_or_update_pr(wt_path, branch, base_branch, f"{task_id}: agent changes", body)
        core.merge_pr(wt_path, pr_url)

        # The worktree is no longer needed after the remote squash merge.
        core.remove_worktree(root, wt_path)
        core.delete_local_branch(root, branch)

        write_summary(
            summary_path,
            f"{task_id} Run Summary",
            [
                f"- Branch: `{branch}` (merged & deleted)",
                f"- Base branch: `{base_branch}`",
                f"- Commit: `{commit_sha}`",
                f"- PR (auto-merged): `{pr_url}`",
                f"- Log: `{relpath_text(log_path, wt_path)}`",
            ],
        )
        brief_relpath = metadata["parent_brief"]
        if brief_relpath:
            brief_path = resolve_feature_brief(root, brief_relpath)
            if brief_path is not None:
                archive_feature_brief_if_complete(
                    root,
                    brief_path,
                    note=f"Archived after `{task_id}` completed and no open tasks remained.",
                )
        move_task(root, running_task, "completed", f"Auto-merged into `{base_branch}`: {pr_url}")
        print(f"task complete: {task_id}")
        print(f"branch: {branch} (merged)")
        print(f"commit: {commit_sha}")
        print(f"pr (auto-merged): {pr_url}")
        print(f"log: {log_path}")
        return 0
    except core.AgentError as exc:
        write_summary(summary_path, f"{task_id} Failed", [f"- Error: `{exc}`", f"- Log: `{relpath_text(log_path, wt_path if wt_path.exists() else root)}`"])
        move_task(root, running_task, "failed", str(exc))
        print(f"task failed: {task_id}\nerror: {exc}")
        return 1
    except Exception as exc:
        write_summary(summary_path, f"{task_id} Failed", [f"- Error: `{type(exc).__name__}: {exc}`"])
        move_task(root, running_task, "failed", f"{type(exc).__name__}: {exc}")
        print(f"task failed: {task_id}\nerror: {type(exc).__name__}: {exc}")
        return 1


def command_status(args: argparse.Namespace) -> int:
    root = core.repo_root(Path.cwd())
    ensure_dirs(root)
    for state in TASK_STATES:
        paths = sorted(p for p in (root / ".agent-tasks" / state).glob("*.md") if p.name != "TASK-template.md")
        print(f"{state}: {len(paths)}")
        for path in paths:
            print(f"  - {path.name}")
    return 0


def command_integrate(args: argparse.Namespace) -> int:
    root = core.repo_root(Path.cwd())
    core.ensure_gh()
    cmd = ["gh", "pr", "list", "--state", "open", "--json", "number,title,headRefName,baseRefName,url", "--limit", "50"]
    if args.epic:
        cmd.extend(["--search", args.epic])
    result = core.run(cmd, root, check=True)
    prs = json.loads(result.stdout or "[]")
    stuck = [
        pr for pr in prs
        if any(str(pr.get("headRefName", "")).startswith(prefix) for prefix in core.STANDARD_BRANCH_PREFIXES)
    ]
    if not stuck:
        print("no open topic PRs — all tasks auto-merge on completion.")
        return 0
    print("open topic PRs (expected to auto-merge; these did not):")
    for pr in stuck:
        print(f"  #{pr['number']} {pr['title']} [{pr['headRefName']} -> {pr['baseRefName']}]")
        print(f"     {pr['url']}")
    print("\nRetry a merge with:  gh pr merge <number> --squash --delete-branch")
    return 0


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agent task orchestrator (autopilot)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("plan", help="plan from a main PRD, feature brief, or direct issue description")
    p.add_argument("requirement", nargs="?", help="issue description or product document id/path")
    p.add_argument("--from-prd", dest="from_prd", help="path or PRD id to plan from")
    p.add_argument("--from-brief", dest="from_brief", help="path or feature brief id to plan from")
    p.set_defaults(func=command_plan)

    p = sub.add_parser("run", help="run one task in a worktree, then auto-merge")
    p.add_argument("task", help="task id, for example TASK-001")
    p.set_defaults(func=command_run)

    p = sub.add_parser("status", help="show task status")
    p.set_defaults(func=command_status)

    p = sub.add_parser("integrate", help="list topic PRs that failed to auto-merge")
    p.add_argument("epic", nargs="?", help="optional epic id to search")
    p.set_defaults(func=command_integrate)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
