#!/usr/bin/env python3
"""Minimal context-graph hook scaffold.

This hook is intentionally conservative:
- It never edits source files.
- It records events, card snapshots, and lightweight relation rules.
- It is safe to run from PostToolUse(Write|Edit) and Stop.

Indexing strategy:
- PostToolUse reindexes the single file just written/edited.
- The first Stop sweep does a full scan. Later sweeps reindex only files that
  changed since the last indexed commit (plus the working tree) using git diff,
  and prune cards for deleted files.

The graph is consumed by:
- the ``/context`` slash command (``query`` mode below), and
- a non-blocking systemMessage on Stop listing files flagged for review.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any

PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR", Path.cwd())).resolve()
GRAPH_DIR = PROJECT_DIR / ".context-graph"
EVENT_LOG = GRAPH_DIR / "events.log"
STATE_FILE = GRAPH_DIR / "state.json"
CARDS_DIR = GRAPH_DIR / "cards"
RELATIONS_FILE = GRAPH_DIR / "relations.json"

SKIP_PREFIXES = (
    ".git/",
    ".context-graph/",
    "node_modules/",
    ".venv/",
    "venv/",
    "dist/",
    "build/",
)

TEXT_SUFFIXES = {
    ".md",
    ".txt",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".sh",
    ".ps1",
}

RULES = [
    {
        "name": "command-files",
        "match_prefixes": ["src/commands/", "commands/"],
        "related": [
            {"path": "README.md", "reason": "command docs may need updates"},
            {"path": "docs/cli.md", "reason": "CLI docs may need updates"},
            {"prefix": "tests/", "reason": "tests may need updates"},
        ],
    },
    {
        "name": "config-files",
        "match_prefixes": ["src/config/", "config/"],
        "related": [
            {"path": "docs/config.md", "reason": "config docs may need updates"},
            {"path": ".env.example", "reason": "env example may need updates"},
            {"path": "README.md", "reason": "top-level docs may mention config"},
        ],
    },
    {
        "name": "readme-files",
        "match_paths": ["README.md"],
        "related": [
            {"prefix": "src/commands/", "reason": "README command docs should match implementation"},
            {"prefix": "src/config/", "reason": "README config docs should match implementation"},
        ],
    },
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Git diff support (incremental stop sweep)
# --------------------------------------------------------------------------- #

def _git(args: list[str]) -> str:
    """Run a git command in PROJECT_DIR; return stdout (empty on any failure)."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=PROJECT_DIR,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def _split_nul(text: str) -> list[str]:
    return [item for item in text.split("\0") if item]


def git_head_sha() -> str | None:
    sha = _git(["rev-parse", "HEAD"]).strip()
    return sha or None


def changed_files_since(base_sha: str) -> list[str]:
    """Files changed between *base_sha* and HEAD (committed deltas)."""
    return _split_nul(_git(["diff", "--name-only", "-z", f"{base_sha}..HEAD"]))


def changed_files_working_tree() -> list[str]:
    """Tracked (staged + unstaged) and untracked files in the working tree."""
    files = _split_nul(_git(["diff", "--name-only", "-z", "HEAD"]))
    files.extend(_split_nul(_git(["ls-files", "--others", "--exclude-standard", "-z"])))
    seen: set[str] = set()
    unique: list[str] = []
    for path in files:
        if path and path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def ensure_dirs() -> None:
    GRAPH_DIR.mkdir(exist_ok=True)
    CARDS_DIR.mkdir(exist_ok=True)


def load_payload() -> dict[str, Any]:
    try:
        if sys.stdin.isatty():
            return {}
        raw = sys.stdin.read().strip()
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def read_state() -> dict[str, Any]:
    return read_json(STATE_FILE, {"files": {}, "last_indexed_sha": None, "last_stop_scan": None})


def write_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def read_relations() -> dict[str, Any]:
    return read_json(RELATIONS_FILE, {"outgoing": {}, "incoming": {}})


def write_relations(relations: dict[str, Any]) -> None:
    RELATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    RELATIONS_FILE.write_text(json.dumps(relations, indent=2, ensure_ascii=False), encoding="utf-8")


def append_event(event: dict[str, Any]) -> None:
    EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with EVENT_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()


def looks_like_windows_path(value: str) -> bool:
    return len(value) >= 3 and value[1:3] in (":\\", ":/")


def normalize_rel(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_DIR).as_posix()


def resolve_input_path(file_path: str) -> Path:
    if looks_like_windows_path(file_path):
        windows_path = PureWindowsPath(file_path)
        posix_guess = Path("/" + "/".join(windows_path.parts[1:]))
        if posix_guess.exists():
            return posix_guess.resolve()
        return Path(str(windows_path))

    path = Path(file_path)
    if path.is_absolute():
        return path.resolve()
    return (PROJECT_DIR / path).resolve()


def should_index(path: Path) -> bool:
    try:
        rel = normalize_rel(path)
    except Exception:
        return False

    if any(rel.startswith(prefix) for prefix in SKIP_PREFIXES):
        return False
    if path.is_dir() or not path.exists():
        return False
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return False
    return True


def chunk_file(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    rel = normalize_rel(path)
    cards: list[dict[str, Any]] = []

    cards.append(
        {
            "id": f"file:{rel}",
            "type": "file",
            "file_path": rel,
            "title": path.name,
            "hash": sha1_text(text),
            "preview": text[:400],
        }
    )

    if path.suffix.lower() == ".md":
        heading = "ROOT"
        para_index = 0
        buffer: list[str] = []

        def flush_buffer() -> None:
            nonlocal para_index, buffer
            block = "\n".join(line.rstrip() for line in buffer).strip()
            buffer = []
            if not block:
                return
            para_index += 1
            block_hash = sha1_text(block)[:8]
            cards.append(
                {
                    "id": f"block:{rel}#{heading}:p{para_index}:{block_hash}",
                    "type": "block",
                    "file_path": rel,
                    "title": heading,
                    "hash": block_hash,
                    "preview": block[:400],
                }
            )

        for raw_line in text.splitlines():
            line = raw_line.rstrip()
            if line.startswith("#"):
                flush_buffer()
                heading = line.lstrip("#").strip() or "ROOT"
                para_index = 0
                continue
            if not line.strip():
                flush_buffer()
                continue
            buffer.append(line)

        flush_buffer()
    else:
        lines = text.splitlines()
        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith(("def ", "class ", "function ", "export function ", "export class ")):
                block_hash = sha1_text(f"{idx}:{stripped}")[:8]
                cards.append(
                    {
                        "id": f"symbol:{rel}:L{idx}:{block_hash}",
                        "type": "symbol",
                        "file_path": rel,
                        "title": stripped[:120],
                        "hash": block_hash,
                        "preview": stripped,
                    }
                )

    return cards


def store_cards(path: Path, cards: list[dict[str, Any]]) -> None:
    rel = normalize_rel(path)
    safe_name = rel.replace("/", "__") + ".json"
    target = CARDS_DIR / safe_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(cards, indent=2, ensure_ascii=False), encoding="utf-8")


def list_indexable_files() -> list[str]:
    files: list[str] = []
    for path in PROJECT_DIR.rglob("*"):
        if path.is_file() and should_index(path):
            files.append(normalize_rel(path))
    return sorted(files)


def expand_related(rel_file: str, spec: dict[str, str], all_files: list[str]) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    reason = spec.get("reason", "related file may need review")

    if "path" in spec:
        target = spec["path"]
        if target in all_files and target != rel_file:
            results.append({"path": target, "reason": reason})

    prefix = spec.get("prefix")
    if prefix:
        for candidate in all_files:
            if candidate != rel_file and candidate.startswith(prefix):
                results.append({"path": candidate, "reason": reason})

    return results


def compute_related_files(rel_file: str) -> list[dict[str, str]]:
    all_files = list_indexable_files()
    related: list[dict[str, str]] = []

    for rule in RULES:
        prefixes = rule.get("match_prefixes", [])
        paths = rule.get("match_paths", [])
        matched = rel_file in paths or any(rel_file.startswith(prefix) for prefix in prefixes)
        if not matched:
            continue
        for spec in rule.get("related", []):
            related.extend(expand_related(rel_file, spec, all_files))

    dedup: dict[str, dict[str, str]] = {}
    for item in related:
        dedup[item["path"]] = item
    return sorted(dedup.values(), key=lambda item: item["path"])


def update_relations_for_file(rel_file: str, related: list[dict[str, str]]) -> None:
    relations = read_relations()
    outgoing = relations.setdefault("outgoing", {})
    incoming = relations.setdefault("incoming", {})

    old_targets = [item["path"] for item in outgoing.get(rel_file, [])]
    for target in old_targets:
        incoming[target] = [item for item in incoming.get(target, []) if item.get("path") != rel_file]
        if not incoming[target]:
            incoming.pop(target, None)

    outgoing[rel_file] = related
    for item in related:
        target = item["path"]
        incoming.setdefault(target, [])
        incoming[target] = [entry for entry in incoming[target] if entry.get("path") != rel_file]
        incoming[target].append({"path": rel_file, "reason": item["reason"]})
        incoming[target] = sorted(incoming[target], key=lambda entry: entry["path"])

    write_relations(relations)


def mark_related_cards_dirty(rel_file: str, related: list[dict[str, str]], source: str) -> None:
    if not related:
        return

    state = read_state()
    files = state.setdefault("files", {})
    for item in related:
        target = item["path"]
        record = files.setdefault(target, {})
        record["needs_review"] = True
        record["review_reason"] = item["reason"]
        record["triggered_by"] = rel_file
        record["last_source"] = source
    write_state(state)


def reindex_file(path: Path, source: str) -> None:
    if not should_index(path):
        append_event(
            {
                "time": now_iso(),
                "event": "skip",
                "source": source,
                "file": str(path),
            }
        )
        return

    cards = chunk_file(path)
    store_cards(path, cards)

    state = read_state()
    rel = normalize_rel(path)
    # The file itself is being re-scanned (it changed or was just written), so
    # any stale "needs_review" flag on it is now addressed.
    state["files"][rel] = {
        "last_indexed_at": now_iso(),
        "card_count": len(cards),
        "content_hash": cards[0]["hash"] if cards else None,
        "needs_review": False,
        "review_reason": None,
        "triggered_by": None,
        "last_source": source,
    }
    write_state(state)

    related = compute_related_files(rel)
    update_relations_for_file(rel, related)
    mark_related_cards_dirty(rel, related, source)

    append_event(
        {
            "time": now_iso(),
            "event": "reindex",
            "source": source,
            "file": rel,
            "card_count": len(cards),
            "related_count": len(related),
        }
    )


def drop_card(rel: str) -> None:
    """Remove a deleted file's card, state entry, and relation edges."""
    safe_name = rel.replace("/", "__") + ".json"
    target = CARDS_DIR / safe_name
    if target.exists():
        try:
            target.unlink()
        except OSError:
            pass

    state = read_state()
    if rel in state.get("files", {}):
        del state["files"][rel]
        write_state(state)

    relations = read_relations()
    outgoing = relations.get("outgoing", {})
    incoming = relations.get("incoming", {})
    changed = False
    if rel in outgoing:
        for item in outgoing[rel]:
            edge_target = item.get("path")
            if edge_target and edge_target in incoming:
                incoming[edge_target] = [e for e in incoming[edge_target] if e.get("path") != rel]
                if not incoming[edge_target]:
                    incoming.pop(edge_target, None)
        outgoing.pop(rel, None)
        changed = True
    if changed:
        write_relations(relations)


def stop_sweep() -> None:
    """Reindex changed files only.

    First run (or when no HEAD commit exists yet) does a full scan. After that,
    only files changed since the last indexed commit (plus the current working
    tree) are re-scanned. Deleted files have their cards pruned.
    """
    state = read_state()
    files_state = state.setdefault("files", {})
    sha = git_head_sha()
    last_sha = state.get("last_indexed_sha")
    do_full = (not last_sha) or (sha is None) or (not files_state)

    if do_full:
        targets = list_indexable_files()
        source = "stop-full"
    else:
        candidates: set[str] = set()
        candidates.update(changed_files_since(last_sha))  # type: ignore[arg-type]
        candidates.update(changed_files_working_tree())
        targets = []
        for rel in sorted(candidates):
            if should_index(PROJECT_DIR / rel):
                targets.append(rel)
            else:
                drop_card(rel)
        source = "stop-incremental"

    for rel in targets:
        reindex_file(PROJECT_DIR / rel, source=source)

    state = read_state()
    if sha:
        state["last_indexed_sha"] = sha
    state["last_stop_scan"] = {
        "time": now_iso(),
        "mode": source,
        "count": len(targets),
        "files": targets,
    }
    write_state(state)

    append_event(
        {
            "time": now_iso(),
            "event": "stop_sweep_complete",
            "mode": source,
            "count": len(targets),
        }
    )

    surface_dirty_files()


def surface_dirty_files() -> None:
    """Non-blocking consumer: tell the user which related files may need review."""
    state = read_state()
    files = state.get("files", {})
    dirty = sorted(rel for rel, rec in files.items() if rec.get("needs_review"))
    if not dirty:
        return
    listed = ", ".join(f"`{p}`" for p in dirty[:10])
    extra = f" and {len(dirty) - 10} more" if len(dirty) > 10 else ""
    sys.stdout.write(
        json.dumps(
            {"systemMessage": f"Context graph: related files may need review — {listed}{extra}. Run `/context` for details."},
            ensure_ascii=False,
        )
        + "\n"
    )
    sys.stdout.flush()


def query_mode(target_arg: str | None) -> int:
    """Active consumer: print files related to the given path or current changes."""
    relations = read_relations()
    state = read_state()
    outgoing = relations.get("outgoing", {})
    incoming = relations.get("incoming", {})

    if target_arg:
        path = resolve_input_path(target_arg)
        try:
            targets = [normalize_rel(path)]
        except Exception:
            print(f"not under project root: {target_arg}")
            return 1
    else:
        targets = changed_files_working_tree()
        if not targets:
            print("No working-tree changes detected. Pass a file path to query a specific file.")
            return 0

    print("Queried files:")
    for rel in targets:
        print(f"  - {rel}")

    related: dict[str, str] = {}
    for rel in targets:
        for item in outgoing.get(rel, []):
            edge = item.get("path")
            if edge and edge != rel:
                related.setdefault(edge, item.get("reason", "related"))
        for item in incoming.get(rel, []):
            edge = item.get("path")
            if edge and edge != rel:
                related.setdefault(edge, item.get("reason", "related"))

    print("\nRelated files (from context graph):")
    if related:
        for path in sorted(related):
            print(f"  - {path}  ({related[path]})")
    else:
        print("  (none recorded)")

    needs_review = sorted(
        rel for rel, rec in state.get("files", {}).items() if rec.get("needs_review")
    )
    if needs_review:
        print("\nFlagged for review (related to recent edits, not yet changed):")
        for rel in needs_review:
            print(f"  - {rel}")

    return 0


def post_tool_use(payload: dict[str, Any]) -> None:
    tool_input = payload.get("tool_input", {})
    file_path = tool_input.get("file_path")
    if not file_path:
        append_event(
            {
                "time": now_iso(),
                "event": "post_tool_use_missing_path",
                "payload_keys": sorted(payload.keys()),
            }
        )
        return

    path = resolve_input_path(file_path)
    if path.exists():
        reindex_file(path, source="post-tool-use")
    else:
        append_event(
            {
                "time": now_iso(),
                "event": "missing_file",
                "source": "post-tool-use",
                "file": str(path),
            }
        )


def main() -> int:
    ensure_dirs()
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    payload = load_payload()

    if mode == "post-tool-use":
        post_tool_use(payload)
        return 0

    if mode == "stop":
        stop_sweep()
        return 0

    if mode == "query":
        target = sys.argv[2] if len(sys.argv) > 2 else None
        return query_mode(target)

    append_event({"time": now_iso(), "event": "unknown_mode", "mode": mode})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
