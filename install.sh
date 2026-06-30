#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: install.sh [--profile generic|node|python] [--force]

Install the Claude Code agent environment template into the current repository.

Options:
  --profile   Profile to append. Default: generic.
  --force     Overwrite existing files when copying template files.
  -h, --help  Show this help.
USAGE
}

PROFILE="generic"
FORCE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      if [[ $# -lt 2 ]]; then
        echo "error: --profile requires generic, node, or python" >&2
        exit 2
      fi
      PROFILE="$2"
      shift 2
      ;;
    --force)
      FORCE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

case "$PROFILE" in
  generic|node|python) ;;
  *)
    echo "error: unsupported profile: $PROFILE" >&2
    exit 2
    ;;
esac

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE_SRC="$SCRIPT_DIR/template"
PROFILE_FILE="$SCRIPT_DIR/profiles/$PROFILE/claude.append.md"

if [[ ! -d "$TEMPLATE_SRC" ]]; then
  echo "error: template directory not found: $TEMPLATE_SRC" >&2
  exit 1
fi

if [[ ! -f "$PROFILE_FILE" ]]; then
  echo "error: profile file not found: $PROFILE_FILE" >&2
  exit 1
fi

if git_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  TARGET_ROOT="$git_root"
else
  TARGET_ROOT="$(pwd)"
  echo "warning: current directory is not inside a Git repository; installing into $TARGET_ROOT" >&2
fi

copy_file() {
  local src="$1"
  local rel="$2"
  local dest="$TARGET_ROOT/$rel"

  mkdir -p -- "$(dirname -- "$dest")"

  if [[ -e "$dest" && "$FORCE" -ne 1 ]]; then
    echo "skip existing: $rel"
    return 0
  fi

  cp -- "$src" "$dest"
  echo "installed: $rel"
}

while IFS= read -r -d '' src; do
  rel="${src#"$TEMPLATE_SRC"/}"
  copy_file "$src" "$rel"
done < <(find "$TEMPLATE_SRC" -type f -print0 | sort -z)

CLAUDE_FILE="$TARGET_ROOT/CLAUDE.md"
BEGIN_MARKER="<!-- agent-env-template profile:$PROFILE begin -->"
END_MARKER="<!-- agent-env-template profile:$PROFILE end -->"

if [[ ! -f "$CLAUDE_FILE" ]]; then
  touch "$CLAUDE_FILE"
fi

if grep -Fq "$BEGIN_MARKER" "$CLAUDE_FILE"; then
  echo "profile already appended: $PROFILE"
else
  {
    printf '\n%s\n' "$BEGIN_MARKER"
    cat "$PROFILE_FILE"
    printf '\n%s\n' "$END_MARKER"
  } >> "$CLAUDE_FILE"
  echo "appended profile rules: $PROFILE"
fi

chmod +x "$TARGET_ROOT/.claude/hooks/stop-auto-pr.py" 2>/dev/null || true
chmod +x "$TARGET_ROOT/orchestrator/agent-team.py" 2>/dev/null || true
chmod +x "$TARGET_ROOT/install-skills.sh" 2>/dev/null || true

# Install bundled skills
if [[ -x "$TARGET_ROOT/install-skills.sh" ]]; then
  echo ""
  echo "Installing bundled skills..."
  "$TARGET_ROOT/install-skills.sh"
fi

cat <<NEXT_STEPS

Agent environment template installed into:
  $TARGET_ROOT

Next steps:
  1. Confirm GitHub CLI authentication:
     gh auth status

  2. Define product work:
     /prd <idea-or-product-change>

  3. Plan engineering work:
     python orchestrator/agent-team.py plan --from-prd docs/prd/active/PRD-001.md
     # or: python orchestrator/agent-team.py plan --from-brief docs/prd/changes/active/FEATURE-001.md
     # or: python orchestrator/agent-team.py plan "Fix login button not responding"

  4. Run a task (auto-verifies, commits, pushes, opens PR, and merges):
     python orchestrator/agent-team.py run TASK-001

  5. Or just edit files and stop — the Stop hook handles the rest.

Installed skills: agent-product-consultant, agent-planner,
  agent-worker, agent-reviewer, agent-integrator, gh-fix-ci

Mode: autopilot
  - Permissions: bypassPermissions (no confirmation prompts)
  - Auto-merge: PRs merge automatically once verification passes
  - Main branch: agents may commit and merge into main
  - Secret guardrail: .env and secret-like paths are still blocked
NEXT_STEPS
