from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BASH = shutil.which("bash")
_SKIP_NO_BASH = "bash not available on this platform"


TEMPLATE_DIR = Path(__file__).resolve().parents[1]
LIB_DIR = TEMPLATE_DIR / "lib"
HOOK_PATH = TEMPLATE_DIR / ".claude" / "hooks" / "stop-auto-pr.py"


def _load_module(name: str, path: Path):
    """Load a Python module from *path* registered as *name* in sys.modules."""
    sys.path.insert(0, str(path.parent)) if name == "agent_core" else None
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Ensure lib/ is importable for the hook's ``import agent_core``.
sys.path.insert(0, str(LIB_DIR))

core = _load_module("agent_core", LIB_DIR / "agent_core.py")
hook = _load_module("stop_auto_pr", HOOK_PATH)


# =================================================================== #
# agent_core — the shared library
# =================================================================== #

class AgentCoreTests(unittest.TestCase):

    # -- secret guardrail -------------------------------------------- #

    def test_is_sensitive_path(self) -> None:
        self.assertTrue(core.is_sensitive_path(".env"))
        self.assertTrue(core.is_sensitive_path(".env.local"))
        self.assertTrue(core.is_sensitive_path("config/.env.example"))
        self.assertTrue(core.is_sensitive_path("app/secrets/private.key"))
        self.assertTrue(core.is_sensitive_path("app/api-secret.json"))
        self.assertTrue(core.is_sensitive_path("app/api-token.json"))
        self.assertFalse(core.is_sensitive_path("docs/readme.md"))
        self.assertFalse(core.is_sensitive_path("src/settings.py"))

    def test_git_branch_and_commit_policy(self) -> None:
        self.assertTrue(core.is_standard_topic_branch("feature/user-auth"))
        self.assertTrue(core.is_standard_topic_branch("fix/issue-42"))
        self.assertTrue(core.is_standard_topic_branch("refactor/api-client"))
        self.assertTrue(core.is_standard_topic_branch("chore/add-lint"))
        self.assertFalse(core.is_standard_topic_branch("agent/task-1"))
        self.assertFalse(core.is_standard_topic_branch("main"))
        self.assertFalse(core.is_standard_topic_branch("feature/"))
        self.assertEqual(core.conventional_commit_subject("feat", "add login"), "feat: add login")
        self.assertEqual(
            core.conventional_commit_subject("fix", "handle crash", "auth"),
            "fix(auth): handle crash",
        )
        with self.assertRaises(core.AgentError):
            core.validate_conventional_commit_subject("TASK-001: worker changes")

    def test_is_runtime_artifact(self) -> None:
        self.assertTrue(core.is_runtime_artifact(".agent-runs/stop-123.jsonl"))
        self.assertTrue(core.is_runtime_artifact(".agent-runs"))
        self.assertFalse(core.is_runtime_artifact("src/app.py"))
        self.assertFalse(core.is_runtime_artifact("agent-runs/foo"))

    def test_force_push_is_blocked_for_protected_branch(self) -> None:
        with mock.patch.object(core, "ensure_origin"), mock.patch.object(core, "run") as run:
            with self.assertRaises(core.AgentError):
                core.push_branch(Path("."), "main", force_with_lease=True)
        run.assert_not_called()

    def test_orchestrator_commit_all_checks_sensitive_paths_before_staging(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "agent_team", str(TEMPLATE_DIR / "orchestrator" / "agent-team.py")
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["agent_team"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            with mock.patch.object(mod.core, "changed_paths", return_value=[".env.local"]), mock.patch.object(
                mod.core, "run"
            ) as run:
                with self.assertRaises(core.AgentError):
                    mod.commit_all(repo, "fix: update config")
            run.assert_not_called()


    def test_detect_node_scripts_skipping_noop_test(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "package.json").write_text(
                json.dumps({
                    "scripts": {
                        "test": "echo \"Error: no test specified\" && exit 1",
                        "lint": "eslint .",
                    }
                }),
                encoding="utf-8",
            )
            commands = core.detect_verification_commands(repo)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].args, ["npm", "run", "lint"])

    def test_detect_prefers_pnpm_and_includes_python(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "package.json").write_text(
                json.dumps({
                    "name": "demo",
                    "scripts": {
                        "lint": "eslint .",
                        "typecheck": "tsc --noEmit",
                        "test": "vitest",
                    },
                }),
                encoding="utf-8",
            )
            (repo / "pnpm-lock.yaml").write_text("lockfileVersion: 9\n", encoding="utf-8")
            (repo / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")

            def fake_which(name: str) -> str | None:
                return {"ruff": "ruff", "pytest": "pytest", "mypy": None, "tox": None}.get(name)

            with mock.patch.object(core.shutil, "which", side_effect=fake_which):
                commands = core.detect_verification_commands(repo)

        self.assertEqual(
            [c.args for c in commands],
            [
                ["pnpm", "run", "lint"],
                ["pnpm", "run", "typecheck"],
                ["pnpm", "run", "test"],
                ["ruff", "check", "."],
                ["pytest"],
            ],
        )

    def test_detect_mypy_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n", encoding="utf-8")
            (repo / "tests").mkdir()

            def fake_which(name: str) -> str | None:
                return {"mypy": "/usr/bin/mypy", "ruff": None, "pytest": None}.get(name)

            with mock.patch.object(core.shutil, "which", side_effect=fake_which):
                commands = core.detect_verification_commands(repo)

        labels = [c.label for c in commands]
        self.assertIn("python:mypy", labels)

    # -- verification run -------------------------------------------- #

    def test_run_verification_passes_when_no_commands(self) -> None:
        results, ok = core.run_verification(Path("."), [])
        self.assertTrue(ok)
        self.assertEqual(results, [])

    def test_run_verification_fails_on_nonzero_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            # Create a marker file so the "command" can check the cwd exists.
            (repo / "marker.txt").touch()
            commands = [core.VerifyCommand("test:fail", ["python", "-c", "import sys; sys.exit(1)"])]
            results, ok = core.run_verification(repo, commands)
        self.assertFalse(ok)
        self.assertEqual(results[0].returncode, 1)

    # -- verification report ----------------------------------------- #

    def test_verification_report_formatting(self) -> None:
        commands = [
            core.VerifyCommand("node:lint", ["npm", "run", "lint"]),
            core.VerifyCommand("python:pytest", ["pytest"]),
        ]
        from agent_core import VerificationResult
        results = [
            VerificationResult(commands[0], 0, "", ""),
            VerificationResult(commands[1], 1, "FAIL\n", ""),
        ]
        report = core.verification_report(results, commands)
        self.assertIn("PASS: `npm run lint`", report)
        self.assertIn("FAIL: `pytest`", report)


# =================================================================== #
# stop-auto-pr hook (protocol + integration with core)
# =================================================================== #

class HookProtocolTests(unittest.TestCase):

    def test_done_exits_zero_with_message(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            hook.done("all good")
        self.assertEqual(ctx.exception.code, 0)

    def test_done_exits_zero_without_message(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            hook.done()
        self.assertEqual(ctx.exception.code, 0)

    def test_block_exits_two(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            hook.block("bad secrets")
        self.assertEqual(ctx.exception.code, 2)

    def test_hook_shares_core_sensitive_path_logic(self) -> None:
        # The hook imports agent_core; verify it delegates correctly.
        self.assertTrue(core.is_sensitive_path(".env"))
        self.assertFalse(core.is_sensitive_path("README.md"))

    def test_hook_runtime_artifact_path(self) -> None:
        self.assertTrue(core.is_runtime_artifact(".agent-runs/stop-abc.jsonl"))
        self.assertFalse(core.is_runtime_artifact("src/main.py"))


# =================================================================== #
# orchestrator (lightweight: just verify import + CLI parsing)
# =================================================================== #

class OrchestratorTests(unittest.TestCase):

    def test_import_succeeds(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "agent_team",
            str(TEMPLATE_DIR / "orchestrator" / "agent-team.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["agent_team"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        self.assertTrue(hasattr(mod, "build_parser"))
        parser = mod.build_parser()
        # Verify all subcommands are present via choices dict.
        sub_action = next(
            a for a in parser._actions
            if a.__class__.__name__ == "_SubParsersAction"
        )
        self.assertEqual(set(sub_action.choices), {"plan", "run", "status", "integrate"})

    def test_parse_verification_from_task(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "agent_team",
            str(TEMPLATE_DIR / "orchestrator" / "agent-team.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["agent_team"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        text = "## Verification Commands\n\n- `npm test`\n- `ruff check .`\n"
        cmds = mod.parse_verification_from_task(text)
        self.assertEqual(len(cmds), 2)
        self.assertEqual(cmds[0].args, ["npm", "test"])
        self.assertEqual(cmds[1].args, ["ruff", "check", "."])

    def test_plan_parser_supports_from_prd(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "agent_team",
            str(TEMPLATE_DIR / "orchestrator" / "agent-team.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["agent_team"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        parser = mod.build_parser()
        args = parser.parse_args(["plan", "--from-prd", "docs/prd/active/PRD-001.md"])
        self.assertEqual(args.command, "plan")
        self.assertEqual(args.from_prd, "docs/prd/active/PRD-001.md")
        self.assertIsNone(args.requirement)

    def test_plan_parser_supports_from_brief(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "agent_team",
            str(TEMPLATE_DIR / "orchestrator" / "agent-team.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["agent_team"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        parser = mod.build_parser()
        args = parser.parse_args(["plan", "--from-brief", "docs/prd/changes/active/FEATURE-001.md"])
        self.assertEqual(args.command, "plan")
        self.assertEqual(args.from_brief, "docs/prd/changes/active/FEATURE-001.md")
        self.assertIsNone(args.requirement)

    def test_plan_parser_still_supports_direct_issue(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "agent_team",
            str(TEMPLATE_DIR / "orchestrator" / "agent-team.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["agent_team"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        parser = mod.build_parser()
        args = parser.parse_args(["plan", "fix login button not responding"])
        self.assertEqual(args.command, "plan")
        self.assertEqual(args.requirement, "fix login button not responding")
        self.assertIsNone(args.from_prd)

    def test_classify_plan_input_routes_issue_and_product_work(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "agent_team",
            str(TEMPLATE_DIR / "orchestrator" / "agent-team.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["agent_team"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        self.assertEqual(mod.classify_plan_input("fix login button not responding"), "direct_issue")
        self.assertEqual(mod.classify_plan_input("新增团队邀请功能"), "requires_prd")

    def test_classify_prd_request_routes_main_brief_and_issue(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "agent_team",
            str(TEMPLATE_DIR / "orchestrator" / "agent-team.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["agent_team"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        self.assertEqual(mod.classify_prd_request("修复登录按钮无响应", has_main_prd=True), "direct_issue")
        self.assertEqual(mod.classify_prd_request("修改产品目标和目标用户", has_main_prd=True), "main_prd")
        self.assertEqual(mod.classify_prd_request("新增团队邀请功能", has_main_prd=True), "feature_brief")
        self.assertEqual(mod.classify_prd_request("我要做一个新项目", has_main_prd=False), "main_prd")

    def test_resolve_latest_prd_prefers_newest(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "agent_team",
            str(TEMPLATE_DIR / "orchestrator" / "agent-team.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["agent_team"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs" / "prd" / "active").mkdir(parents=True)
            old = root / "docs" / "prd" / "active" / "PRD-001.md"
            new = root / "docs" / "prd" / "active" / "PRD-002.md"
            old.write_text("old", encoding="utf-8")
            new.write_text("new", encoding="utf-8")
            os.utime(old, (1, 1))
            os.utime(new, (2, 2))
            self.assertEqual(mod.resolve_latest_prd(root), old)
            self.assertEqual(mod.resolve_main_prd(root), old)

    def test_resolve_prd_reference_supports_id_and_repo_relative_path(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "agent_team",
            str(TEMPLATE_DIR / "orchestrator" / "agent-team.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["agent_team"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prd = root / "docs" / "prd" / "active" / "PRD-001.md"
            prd.parent.mkdir(parents=True)
            prd.write_text("demo", encoding="utf-8")
            self.assertEqual(mod.resolve_prd_reference(root, "PRD-001"), prd.resolve())
            self.assertEqual(mod.resolve_prd_reference(root, "docs/prd/active/PRD-001.md"), prd.resolve())

    def test_resolve_feature_brief_reference_supports_id_and_repo_relative_path(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "agent_team",
            str(TEMPLATE_DIR / "orchestrator" / "agent-team.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["agent_team"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brief = root / "docs" / "prd" / "changes" / "active" / "FEATURE-001.md"
            brief.parent.mkdir(parents=True)
            brief.write_text("demo", encoding="utf-8")
            self.assertEqual(mod.resolve_feature_brief_reference(root, "FEATURE-001"), brief.resolve())
            self.assertEqual(mod.resolve_feature_brief(root, "FEATURE-001"), brief.resolve())
            self.assertEqual(mod.resolve_feature_brief_reference(root, "docs/prd/changes/active/FEATURE-001.md"), brief.resolve())

    def test_ensure_dirs_creates_prd_directories(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "agent_team",
            str(TEMPLATE_DIR / "orchestrator" / "agent-team.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["agent_team"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mod.ensure_dirs(root)
            self.assertTrue((root / "docs" / "prd" / "active").is_dir())
            self.assertTrue((root / "docs" / "prd" / "completed").is_dir())
            self.assertTrue((root / "docs" / "prd" / "changes" / "active").is_dir())
            self.assertTrue((root / "docs" / "prd" / "changes" / "completed").is_dir())

    def test_archive_feature_brief_if_complete_moves_file(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "agent_team",
            str(TEMPLATE_DIR / "orchestrator" / "agent-team.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["agent_team"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brief = root / "docs" / "prd" / "changes" / "active" / "FEATURE-001.md"
            brief.parent.mkdir(parents=True)
            (root / ".agent-tasks" / "completed").mkdir(parents=True)
            brief.write_text("demo", encoding="utf-8")
            archived = mod.archive_feature_brief_if_complete(root, brief, note="done")
            self.assertIsNotNone(archived)
            self.assertFalse(brief.exists())
            self.assertTrue((root / "docs" / "prd" / "changes" / "completed" / "FEATURE-001.md").exists())

    def test_archive_feature_brief_if_complete_skips_main_prd(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "agent_team",
            str(TEMPLATE_DIR / "orchestrator" / "agent-team.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["agent_team"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prd = root / "docs" / "prd" / "active" / "PRD-001.md"
            prd.parent.mkdir(parents=True)
            prd.write_text("demo", encoding="utf-8")
            archived = mod.archive_feature_brief_if_complete(root, prd)
            self.assertIsNone(archived)
            self.assertTrue(prd.exists())

    def test_archive_feature_brief_if_complete_skips_when_open_tasks_exist(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "agent_team",
            str(TEMPLATE_DIR / "orchestrator" / "agent-team.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["agent_team"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brief = root / "docs" / "prd" / "changes" / "active" / "FEATURE-001.md"
            brief.parent.mkdir(parents=True)
            task_dir = root / ".agent-tasks" / "active"
            task_dir.mkdir(parents=True)
            brief.write_text("demo", encoding="utf-8")
            (task_dir / "TASK-001.md").write_text("## Parent Brief\n\n- Brief: `docs/prd/changes/active/FEATURE-001.md`\n", encoding="utf-8")
            archived = mod.archive_feature_brief_if_complete(root, brief)
            self.assertIsNone(archived)
            self.assertTrue(brief.exists())

    def test_task_template_contains_parent_prd(self) -> None:
        text = (TEMPLATE_DIR / ".agent-tasks" / "active" / "TASK-template.md").read_text(encoding="utf-8")
        self.assertIn("## Work Type", text)
        self.assertIn("product | feature | issue", text)
        self.assertIn("## Requirements Source", text)
        self.assertIn("## Parent PRD", text)
        self.assertIn("- PRD: `N/A`", text)
        self.assertIn("## Parent Brief", text)
        self.assertIn("- Brief: `N/A`", text)

    def test_main_prd_prompt_contains_single_source_of_truth_language(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "agent_team",
            str(TEMPLATE_DIR / "orchestrator" / "agent-team.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["agent_team"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        prompt = mod.build_main_prd_plan_prompt("demo prd", "docs/prd/active/PRD-001.md", "EPIC-001")
        self.assertIn("single source of truth", prompt)
        self.assertIn("Work Type `product`", prompt)

    def test_feature_brief_prompt_mentions_parent_prd(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "agent_team",
            str(TEMPLATE_DIR / "orchestrator" / "agent-team.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["agent_team"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        prompt = mod.build_feature_brief_plan_prompt(
            "main prd",
            "docs/prd/active/PRD-001.md",
            "brief",
            "docs/prd/changes/active/FEATURE-001.md",
            "EPIC-001",
        )
        self.assertIn("parent PRD", prompt)
        self.assertIn("Work Type `feature`", prompt)

    def test_issue_prompt_does_not_require_prd_ancestry(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "agent_team",
            str(TEMPLATE_DIR / "orchestrator" / "agent-team.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["agent_team"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        prompt = mod.build_issue_plan_prompt("fix login button not responding", "EPIC-001")
        self.assertIn("No PRD or feature brief is required for this route", prompt)
        self.assertIn("Work Type `issue`", prompt)

    def test_validate_task_traceability_rejects_missing_feature_brief(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "agent_team",
            str(TEMPLATE_DIR / "orchestrator" / "agent-team.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["agent_team"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        text = """## Work Type\n\n- Work type: `feature`\n\n## Requirements Source\n\n- Source: `docs/prd/changes/active/FEATURE-001.md`\n\n## Parent PRD\n\n- PRD: `docs/prd/active/PRD-001.md`\n\n## Parent Brief\n\n- Brief: `N/A`\n\n## Parent Epic\n\n- Epic: `EPIC-001`\n\n## Branch\n\nBranch: `feature/TASK-001-demo`\n\n## Base Branch\n\nBase branch: `main`\n"""
        with self.assertRaises(core.AgentError) as ctx:
            mod.validate_task_traceability(text)
        self.assertIn("Feature tasks must set Parent Brief", str(ctx.exception))

    def test_validate_task_traceability_accepts_issue_without_prd(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "agent_team",
            str(TEMPLATE_DIR / "orchestrator" / "agent-team.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["agent_team"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        text = """## Work Type\n\n- Work type: `issue`\n\n## Requirements Source\n\n- Source: `direct request`\n\n## Parent PRD\n\n- PRD: `N/A`\n\n## Parent Brief\n\n- Brief: `N/A`\n\n## Parent Epic\n\n- Epic: `EPIC-001`\n\n## Branch\n\nBranch: `feature/TASK-001-demo`\n\n## Base Branch\n\nBase branch: `main`\n"""
        metadata = mod.validate_task_traceability(text)
        self.assertEqual(metadata["work_type"], "issue")


# =================================================================== #
# install.sh (dry-run)
# =================================================================== #

class InstallScriptTests(unittest.TestCase):

    @unittest.skipUnless(BASH, _SKIP_NO_BASH)
    def test_install_sh_is_valid_bash(self) -> None:
        script = TEMPLATE_DIR.parent / "install.sh"
        self.assertTrue(script.exists(), f"install.sh not found at {script}")
        result = subprocess.run(
            [BASH, "-n", str(script)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(result.returncode, 0, f"bash -n failed: {result.stderr}")

    @unittest.skipUnless(BASH, _SKIP_NO_BASH)
    def test_install_skills_dry_run(self) -> None:
        script = TEMPLATE_DIR / "install-skills.sh"
        self.assertTrue(script.exists())
        result = subprocess.run(
            [BASH, str(script), "--dry-run"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(result.returncode, 0, f"dry-run failed: {result.stderr}\n{result.stdout}")
        self.assertIn("~/.claude/skills/", result.stdout)


if __name__ == "__main__":
    unittest.main()
