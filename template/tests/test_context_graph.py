from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TEMPLATE_DIR = Path(__file__).resolve().parents[1]
HOOK_PATH = TEMPLATE_DIR / ".claude" / "hooks" / "context_graph_hook.py"


def load_hook(project_dir: Path):
    """Load the hook fresh so PROJECT_DIR picks up the temp repo via env."""
    os.environ["CLAUDE_PROJECT_DIR"] = str(project_dir)
    spec = importlib.util.spec_from_file_location("context_graph_hook_under_test", str(HOOK_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["context_graph_hook_under_test"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


class ContextGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(["init", "-q"], self.root)
        git(["config", "user.email", "t@t.test"], self.root)
        git(["config", "user.name", "test"], self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()
        os.environ.pop("CLAUDE_PROJECT_DIR", None)

    def _write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_first_sweep_is_full_and_records_sha(self) -> None:
        self._write("src/commands/foo.py", "def run():\n    pass\n")
        git(["add", "-A"], self.root)
        git(["commit", "-q", "-m", "init"], self.root)

        hook = load_hook(self.root)
        hook.stop_sweep()

        state = hook.read_state()
        self.assertEqual(state["last_stop_scan"]["mode"], "stop-full")
        self.assertTrue(state.get("last_indexed_sha"))
        self.assertTrue((hook.CARDS_DIR / "src__commands__foo.py.json").exists())

    def test_second_sweep_is_incremental_on_diff(self) -> None:
        self._write("src/commands/a.py", "def a():\n    pass\n")
        self._write("src/commands/b.py", "def b():\n    pass\n")
        git(["add", "-A"], self.root)
        git(["commit", "-q", "-m", "two"], self.root)

        hook = load_hook(self.root)
        hook.stop_sweep()  # full

        # Change only b.py in a new commit.
        self._write("src/commands/b.py", "def b():\n    return 1\n")
        git(["add", "-A"], self.root)
        git(["commit", "-q", "-m", "b2"], self.root)
        hook.stop_sweep()  # incremental

        state = hook.read_state()
        self.assertEqual(state["last_stop_scan"]["mode"], "stop-incremental")
        self.assertEqual(state["last_stop_scan"]["count"], 1)
        self.assertEqual(state["last_stop_scan"]["files"], ["src/commands/b.py"])

    def test_deleted_file_prunes_card(self) -> None:
        self._write("src/commands/a.py", "def a():\n    pass\n")
        git(["add", "-A"], self.root)
        git(["commit", "-q", "-m", "init"], self.root)

        hook = load_hook(self.root)
        hook.stop_sweep()
        card = hook.CARDS_DIR / "src__commands__a.py.json"
        self.assertTrue(card.exists())

        (self.root / "src/commands/a.py").unlink()
        git(["add", "-A"], self.root)
        git(["commit", "-q", "-m", "del"], self.root)
        hook.stop_sweep()

        self.assertFalse(card.exists())
        self.assertNotIn("src/commands/a.py", hook.read_state().get("files", {}))

    def test_query_surfaces_related_files(self) -> None:
        self._write("README.md", "# project\n")
        self._write("src/commands/foo.py", "def run():\n    pass\n")
        git(["add", "-A"], self.root)
        git(["commit", "-q", "-m", "init"], self.root)

        hook = load_hook(self.root)
        hook.stop_sweep()  # builds relations: src/commands/* -> README.md

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = hook.query_mode("src/commands/foo.py")
        self.assertEqual(rc, 0)
        self.assertIn("README.md", buf.getvalue())

    def test_reindex_clears_own_needs_review_flag(self) -> None:
        # Editing a command file flags README.md; editing README.md then clears its flag.
        self._write("README.md", "# project\n")
        self._write("src/commands/foo.py", "def run():\n    pass\n")
        git(["add", "-A"], self.root)
        git(["commit", "-q", "-m", "init"], self.root)

        hook = load_hook(self.root)
        hook.reindex_file(self.root / "src/commands/foo.py", source="post-tool-use")
        state = hook.read_state()
        self.assertTrue(state["files"]["README.md"].get("needs_review"))

        hook.reindex_file(self.root / "README.md", source="post-tool-use")
        state = hook.read_state()
        self.assertFalse(state["files"]["README.md"].get("needs_review"))

    # ------------------------------------------------------------------ #
    # Additional hook behavior
    # ------------------------------------------------------------------ #

    def test_post_tool_use_indexes_file_from_payload(self) -> None:
        self._write("src/commands/foo.py", "def run():\n    pass\n")
        git(["add", "-A"], self.root)
        git(["commit", "-q", "-m", "init"], self.root)

        hook = load_hook(self.root)
        hook.post_tool_use({"tool_input": {"file_path": str(self.root / "src/commands/foo.py")}})
        self.assertTrue((hook.CARDS_DIR / "src__commands__foo.py.json").exists())

    def test_post_tool_use_logs_missing_file_path(self) -> None:
        hook = load_hook(self.root)
        hook.post_tool_use({})  # payload without file_path
        log = hook.EVENT_LOG.read_text(encoding="utf-8")
        self.assertIn("post_tool_use_missing_path", log)

    def test_main_dispatches_query_stop_and_unknown(self) -> None:
        self._write("README.md", "# project\n")
        self._write("src/commands/foo.py", "def run():\n    pass\n")
        git(["add", "-A"], self.root)
        git(["commit", "-q", "-m", "init"], self.root)
        hook = load_hook(self.root)
        hook.stop_sweep()  # build the graph

        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["hook", "query", "src/commands/foo.py"]), \
                mock.patch.object(sys, "stdin", io.StringIO("")), \
                contextlib.redirect_stdout(buf):
            self.assertEqual(hook.main(), 0)
        self.assertIn("README.md", buf.getvalue())

        with mock.patch.object(sys, "argv", ["hook", "stop"]), \
                mock.patch.object(sys, "stdin", io.StringIO("")):
            self.assertEqual(hook.main(), 0)

        with mock.patch.object(sys, "argv", ["hook", "totally-unknown"]), \
                mock.patch.object(sys, "stdin", io.StringIO("")):
            self.assertEqual(hook.main(), 0)

    def test_incremental_sweep_picks_up_untracked_file(self) -> None:
        self._write("src/commands/a.py", "def a():\n    pass\n")
        git(["add", "-A"], self.root)
        git(["commit", "-q", "-m", "init"], self.root)
        hook = load_hook(self.root)
        hook.stop_sweep()

        self._write("src/commands/b.py", "def b():\n    pass\n")  # untracked, not committed
        hook.stop_sweep()
        state = hook.read_state()
        self.assertEqual(state["last_stop_scan"]["mode"], "stop-incremental")
        self.assertIn("src/commands/b.py", state["last_stop_scan"]["files"])

    def test_incremental_sweep_picks_up_uncommitted_modification(self) -> None:
        self._write("src/commands/a.py", "def a():\n    pass\n")
        git(["add", "-A"], self.root)
        git(["commit", "-q", "-m", "init"], self.root)
        hook = load_hook(self.root)
        hook.stop_sweep()

        self._write("src/commands/a.py", "def a():\n    return 1\n")  # modified, not committed
        hook.stop_sweep()
        state = hook.read_state()
        self.assertEqual(state["last_stop_scan"]["mode"], "stop-incremental")
        self.assertEqual(state["last_stop_scan"]["files"], ["src/commands/a.py"])

    def test_no_commit_repo_does_full_sweep_without_crash(self) -> None:
        self._write("src/commands/a.py", "def a():\n    pass\n")
        # deliberately no git add/commit
        hook = load_hook(self.root)
        hook.stop_sweep()  # must not raise
        state = hook.read_state()
        self.assertEqual(state["last_stop_scan"]["mode"], "stop-full")
        self.assertIsNone(state.get("last_indexed_sha"))

    def test_surface_dirty_files_emits_then_silent(self) -> None:
        hook = load_hook(self.root)
        state = hook.read_state()
        state["files"]["docs/config.md"] = {"needs_review": True}
        hook.write_state(state)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            hook.surface_dirty_files()
        self.assertIn("docs/config.md", buf.getvalue())
        self.assertIn("systemMessage", buf.getvalue())

        state = hook.read_state()
        state["files"]["docs/config.md"]["needs_review"] = False
        hook.write_state(state)
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            hook.surface_dirty_files()
        self.assertEqual(buf2.getvalue(), "")

    def test_query_with_no_changes_returns_zero(self) -> None:
        self._write("README.md", "# project\n")
        git(["add", "-A"], self.root)
        git(["commit", "-q", "-m", "init"], self.root)
        hook = load_hook(self.root)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = hook.query_mode(None)
        self.assertEqual(rc, 0)
        self.assertIn("No working-tree changes", buf.getvalue())

    def test_drop_card_prunes_incoming_edges(self) -> None:
        self._write("README.md", "# project\n")
        self._write("src/commands/foo.py", "def run():\n    pass\n")
        git(["add", "-A"], self.root)
        git(["commit", "-q", "-m", "init"], self.root)
        hook = load_hook(self.root)
        hook.reindex_file(self.root / "src/commands/foo.py", source="post-tool-use")

        relations = hook.read_relations()
        self.assertIn(
            "src/commands/foo.py",
            [e["path"] for e in relations["incoming"].get("README.md", [])],
        )

        hook.drop_card("src/commands/foo.py")
        relations = hook.read_relations()
        self.assertNotIn(
            "src/commands/foo.py",
            [e["path"] for e in relations["incoming"].get("README.md", [])],
        )
        self.assertNotIn("src/commands/foo.py", relations["outgoing"])

    def test_should_index_excludes_ignored_dirs_and_non_text(self) -> None:
        hook = load_hook(self.root)
        good = self._write("src/app.py", "x = 1\n")
        ignored = self._write("node_modules/pkg/lib.py", "x = 1\n")
        graph = self._write(".context-graph/state.json", "{}\n")
        image = self._write("assets/logo.png", "binary")
        self.assertTrue(hook.should_index(good))
        self.assertFalse(hook.should_index(ignored))
        self.assertFalse(hook.should_index(graph))
        self.assertFalse(hook.should_index(image))

    def test_chunk_file_emits_markdown_blocks_and_python_symbols(self) -> None:
        hook = load_hook(self.root)
        md = self._write("docs/guide.md", "# Title\n\nintro paragraph\n\n## Section\n\ntext\n")
        md_cards = hook.chunk_file(md)
        self.assertTrue(any(c["type"] == "block" for c in md_cards))

        py = self._write("src/mod.py", "def foo():\n    pass\n\nclass Bar:\n    pass\n")
        py_cards = hook.chunk_file(py)
        titles = [c.get("title", "") for c in py_cards if c["type"] == "symbol"]
        self.assertTrue(any("def foo" in t for t in titles))
        self.assertTrue(any("class Bar" in t for t in titles))


if __name__ == "__main__":
    unittest.main()
