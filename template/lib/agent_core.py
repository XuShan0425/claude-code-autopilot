#!/usr/bin/env python3
"""Shared core for the orchestrator and the Claude Stop hook.

Both ``orchestrator/agent-team.py`` and ``.claude/hooks/stop-auto-pr.py`` build on
this module so that git, verification, GitHub-PR, secret-guardrail, and logging
logic lives in exactly one place. Callers translate :class:`AgentError` into their
own response protocol (the hook speaks Claude's exit-code protocol; the
orchestrator prints and records the failure).
"""
from __future__ import annotations

import importlib.util
import json
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VERIFY_TIMEOUT_SECONDS = 900

# The one safety rail retained in full-autopilot mode: refuse to ship secrets.
ENV_FILE_PATTERN = re.compile(r"(^|/)\.env(\.|$)")
SECRETS_DIR_PATTERN = re.compile(r"(^|/)secrets(/|$)")
SECRET_KEYWORDS = ("secret", "token")

RUNTIME_ARTIFACT_PREFIX = ".agent-runs/"


class AgentError(Exception):
    """Expected failure (missing tool, failed command, bad state).

    Callers catch this and translate it into their own response format rather
    than letting it surface as an uncaught traceback.
    """


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #

@dataclass
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


@dataclass
class VerifyCommand:
    label: str
    args: list[str]


@dataclass
class VerificationResult:
    command: VerifyCommand
    returncode: int
    stdout: str
    stderr: str


# --------------------------------------------------------------------------- #
# Time / formatting
# --------------------------------------------------------------------------- #

def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def display_cmd(args: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in args)


def tail(text: str, limit: int = 6000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def slug(value: str, fallback: str = "task") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip().lower()).strip("-._")
    return cleaned[:64] or fallback


# --------------------------------------------------------------------------- #
# Process execution
# --------------------------------------------------------------------------- #

def run(
    args: list[str],
    cwd: Path,
    *,
    check: bool = False,
    timeout: int | None = None,
    input_text: str | None = None,
) -> CommandResult:
    """Run a command, never using ``shell=True``.

    On a missing binary or timeout, returns a result with a non-zero returncode
    (127 / 124). When ``check`` is set, such failures raise :class:`AgentError`.
    """
    try:
        completed = subprocess.run(
            args,
            cwd=str(cwd),
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        result = CommandResult(args=args, returncode=127, stdout="", stderr=str(exc))
        if check:
            raise AgentError(f"Command not found: {display_cmd(args)}") from exc
        return result
    except subprocess.TimeoutExpired as exc:
        result = CommandResult(args=args, returncode=124, stdout="", stderr=str(exc))
        if check:
            raise AgentError(f"Command timed out after {timeout}s: {display_cmd(args)}") from exc
        return result

    result = CommandResult(args, completed.returncode, completed.stdout, completed.stderr)
    if check and result.returncode != 0:
        raise AgentError(
            f"Command failed: {display_cmd(args)}\n"
            f"stdout:\n{tail(result.stdout)}\n"
            f"stderr:\n{tail(result.stderr)}"
        )
    return result


# --------------------------------------------------------------------------- #
# Git
# --------------------------------------------------------------------------- #

def repo_root(cwd: Path) -> Path:
    result = run(["git", "rev-parse", "--show-toplevel"], cwd)
    if result.returncode != 0 or not result.stdout.strip():
        raise AgentError("not inside a Git repository")
    return Path(result.stdout.strip()).resolve()


def git_stdout(repo: Path, args: list[str], *, check: bool = True) -> str:
    return run(["git", *args], repo, check=check).stdout.strip()


def current_branch(repo: Path) -> str:
    return git_stdout(repo, ["rev-parse", "--abbrev-ref", "HEAD"])


def ref_exists(repo: Path, ref: str) -> bool:
    return run(["git", "rev-parse", "--verify", "--quiet", ref], repo).returncode == 0


def branch_exists(repo: Path, branch: str) -> bool:
    return ref_exists(repo, f"refs/heads/{branch}")


def remote_ref_exists(repo: Path, branch: str) -> bool:
    return ref_exists(repo, f"refs/remotes/origin/{branch}")


def detect_default_branch(repo: Path) -> str:
    result = run(["git", "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], repo)
    if result.returncode == 0 and result.stdout.strip():
        ref = result.stdout.strip()
        return ref.split("/", 1)[1] if "/" in ref else ref

    result = run(["git", "remote", "show", "origin"], repo)
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            match = re.search(r"HEAD branch:\s*(\S+)", line)
            if match:
                return match.group(1)

    for candidate in ("main", "master"):
        if ref_exists(repo, f"refs/heads/{candidate}") or ref_exists(
            repo, f"refs/remotes/origin/{candidate}"
        ):
            return candidate
    return "main"


# --------------------------------------------------------------------------- #
# JSON / logging
# --------------------------------------------------------------------------- #

def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            parsed = json.load(handle)
        return parsed if isinstance(parsed, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def append_jsonl(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def log_event(log_path: Path | None, kind: str, **data: Any) -> None:
    """Append a timestamped event. No-op when ``log_path`` is None."""
    if log_path is None:
        return
    append_jsonl(
        log_path,
        {"time": datetime.now(timezone.utc).isoformat(), "kind": kind, **data},
    )


# --------------------------------------------------------------------------- #
# Project / tool detection
# --------------------------------------------------------------------------- #

def python_executable() -> str:
    return sys.executable or shutil.which("python3") or shutil.which("python") or "python"


def pyproject_text(repo: Path) -> str:
    try:
        return (repo / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return ""


def has_python_project(repo: Path) -> bool:
    markers = (
        "pyproject.toml", "setup.py", "setup.cfg", "pytest.ini", "tox.ini",
        "Pipfile", "Pipfile.lock", "poetry.lock", "uv.lock",
    )
    if any((repo / marker).exists() for marker in markers):
        return True
    if any(repo.glob("requirements*.txt")):
        return True
    return (repo / "src").is_dir() or (repo / "tests").is_dir()


def ruff_available() -> bool:
    return shutil.which("ruff") is not None


def pytest_available() -> bool:
    if shutil.which("pytest") is not None:
        return True
    try:
        return importlib.util.find_spec("pytest") is not None
    except (ImportError, ValueError):
        return False


def package_manager(repo: Path, package_json: dict[str, Any]) -> str:
    declared = str(package_json.get("packageManager", ""))
    if declared.startswith("pnpm@") or (repo / "pnpm-lock.yaml").exists():
        return "pnpm"
    if declared.startswith("yarn@") or (repo / "yarn.lock").exists():
        return "yarn"
    if declared.startswith("npm@") or (repo / "package-lock.json").exists():
        return "npm"
    if declared.startswith("bun@") or (repo / "bun.lockb").exists() or (repo / "bun.lock").exists():
        return "bun"
    return "npm"


def node_command(pm: str, script_name: str) -> list[str]:
    if pm == "yarn":
        return ["yarn", "run", script_name]
    return [pm, "run", script_name]


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #

def detect_verification_commands(repo: Path) -> list[VerifyCommand]:
    """Detect lint / typecheck / test commands for Node and Python projects.

    Unified so the hook and orchestrator agree. Node scripts come from
    ``package.json`` (skipping npm's default no-op test). Python lint/test run
    when the tool is available; mypy/tox additionally require explicit config.
    """
    commands: list[VerifyCommand] = []

    package_path = repo / "package.json"
    if package_path.exists():
        package = load_json(package_path)
        scripts = package.get("scripts", {})
        if isinstance(scripts, dict):
            pm = package_manager(repo, package)
            for name in ("lint", "typecheck", "test"):
                script = scripts.get(name)
                if isinstance(script, str) and script.strip():
                    if name == "test" and "no test specified" in script.lower() and "exit 1" in script.lower():
                        continue
                    commands.append(VerifyCommand(f"node:{name}", node_command(pm, name)))

    if has_python_project(repo):
        py_text = pyproject_text(repo)
        if ruff_available():
            commands.append(VerifyCommand("python:ruff", ["ruff", "check", "."]))
        if shutil.which("mypy") and ("[tool.mypy" in py_text or (repo / "mypy.ini").exists()):
            commands.append(VerifyCommand("python:mypy", ["mypy", "."]))
        if pytest_available():
            pytest_bin = shutil.which("pytest")
            commands.append(
                VerifyCommand(
                    "python:pytest",
                    [pytest_bin] if pytest_bin else [python_executable(), "-m", "pytest"],
                )
            )
        if (repo / "tox.ini").exists() and shutil.which("tox"):
            commands.append(VerifyCommand("python:tox", ["tox"]))

    seen: set[tuple[str, ...]] = set()
    unique: list[VerifyCommand] = []
    for command in commands:
        key = tuple(command.args)
        if key not in seen:
            seen.add(key)
            unique.append(command)
    return unique


def run_verification(
    repo: Path,
    commands: list[VerifyCommand],
    log_path: Path | None = None,
) -> tuple[list[VerificationResult], bool]:
    """Run commands in order, stopping at the first failure.

    Returns ``(results, all_passed)``. The caller decides how to react to a
    failure (block / mark task failed) so this stays protocol-neutral.
    """
    results: list[VerificationResult] = []
    if not commands:
        log_event(log_path, "verification_skipped", reason="no commands detected")
        return results, True

    for command in commands:
        log_event(log_path, "verification_start", label=command.label, command=command.args)
        result = run(command.args, repo, timeout=VERIFY_TIMEOUT_SECONDS)
        verification = VerificationResult(command, result.returncode, result.stdout, result.stderr)
        results.append(verification)
        log_event(
            log_path,
            "verification_finish",
            label=command.label,
            command=command.args,
            returncode=result.returncode,
            stdout_tail=tail(result.stdout, 4000),
            stderr_tail=tail(result.stderr, 4000),
        )
        if result.returncode != 0:
            return results, False
    return results, True


def verification_report(results: list[VerificationResult], commands: list[VerifyCommand]) -> str:
    if not commands:
        return "- No verification commands were detected."
    lines = []
    for item in results:
        marker = "PASS" if item.returncode == 0 else "FAIL"
        lines.append(f"- {marker}: `{display_cmd(item.command.args)}`")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Secret guardrail (the one rail kept in autopilot mode)
# --------------------------------------------------------------------------- #

def is_runtime_artifact(path: str) -> bool:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized == ".agent-runs" or normalized.startswith(RUNTIME_ARTIFACT_PREFIX)


def is_sensitive_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    if ENV_FILE_PATTERN.search(normalized):
        return True
    if SECRETS_DIR_PATTERN.search(normalized):
        return True
    return any(keyword in normalized for keyword in SECRET_KEYWORDS)


# --------------------------------------------------------------------------- #
# GitHub
# --------------------------------------------------------------------------- #

def ensure_gh() -> None:
    if shutil.which("gh") is None:
        raise AgentError("GitHub CLI 'gh' was not found in PATH.")


def ensure_gh_auth(repo: Path) -> None:
    ensure_gh()
    result = run(["gh", "auth", "status"], repo)
    if result.returncode != 0:
        raise AgentError(
            "GitHub CLI is not authenticated; cannot open, update, or merge a PR.\n"
            f"stdout:\n{tail(result.stdout)}\n"
            f"stderr:\n{tail(result.stderr)}"
        )


def ensure_origin(repo: Path) -> None:
    result = run(["git", "remote", "get-url", "origin"], repo)
    if result.returncode != 0 or not result.stdout.strip():
        raise AgentError("No Git remote named 'origin' is configured; cannot push or open a PR.")


def push_branch(repo: Path, branch: str) -> None:
    ensure_origin(repo)
    run(["git", "push", "-u", "origin", branch], repo, check=True)


def find_existing_pr(repo: Path, branch: str) -> dict[str, Any] | None:
    result = run(
        [
            "gh", "pr", "list", "--head", branch, "--state", "open",
            "--json", "number,url,title", "--limit", "1",
        ],
        repo,
        check=True,
    )
    prs = json.loads(result.stdout or "[]")
    return prs[0] if isinstance(prs, list) and prs else None


def open_or_update_pr(
    repo: Path, branch: str, base: str, title: str, body: str
) -> str:
    import tempfile

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(body)
        body_path = Path(handle.name)
    try:
        existing = find_existing_pr(repo, branch)
        if existing:
            run(
                ["gh", "pr", "edit", str(existing["number"]), "--title", title, "--body-file", str(body_path)],
                repo,
                check=True,
            )
            return str(existing["url"])
        result = run(
            ["gh", "pr", "create", "--base", base, "--head", branch, "--title", title, "--body-file", str(body_path)],
            repo,
            check=True,
        )
        output = result.stdout.strip().splitlines()
        if not output:
            raise AgentError("gh pr create succeeded but returned no URL")
        return output[-1]
    finally:
        try:
            body_path.unlink()
        except OSError:
            pass


def merge_pr(repo: Path, pr_url: str, *, delete_branch: bool = True) -> None:
    """Auto-merge a PR. Autopilot mode: local verification already passed, so we
    merge immediately (squash) rather than waiting on CI."""
    cmd = ["gh", "pr", "merge", pr_url, "--squash"]
    if delete_branch:
        cmd.append("--delete-branch")
    result = run(cmd, repo)
    if result.returncode != 0:
        raise AgentError(
            f"Failed to auto-merge PR {pr_url}.\n"
            "If the repository has branch-protection rules requiring review, disable them "
            "for autopilot mode.\n"
            f"stdout:\n{tail(result.stdout)}\n"
            f"stderr:\n{tail(result.stderr)}"
        )
