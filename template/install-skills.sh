#!/usr/bin/env bash
set -euo pipefail

# Install bundled skills into the local Claude Code skills directory.
# Skips any skill that is already installed (existence check at target path).

usage() {
  cat <<'USAGE'
Usage: install-skills.sh [--dry-run] [--force]

Install bundled skills into ~/.claude/skills/.
Skips skills already present at the target location unless --force is used.

Options:
  --dry-run   Print what would be installed without copying.
  --force     Overwrite existing skill directories.
  -h, --help  Show this help.
USAGE
}

DRY_RUN=0
FORCE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --force) FORCE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "error: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_SRC="$SCRIPT_DIR/skills"

# --- target directory ---
SKILLS_DIR="${SKILLS_DIR:-$HOME/.claude/skills}"

# --- helper ---
install_skill() {
  local src_dir="$1"
  local dst_dir="$2"
  local skill_name="$3"

  local src="$src_dir/$skill_name"
  local dst="$dst_dir/$skill_name"

  if [[ ! -d "$src" ]]; then
    echo "warning: source not found: $src" >&2
    return 1
  fi

  if [[ -d "$dst" || -f "$dst/SKILL.md" ]] && [[ "$FORCE" -ne 1 ]]; then
    echo "skip (exists): $dst"
    return 0
  fi

  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "would install: $skill_name → $dst"
    return 0
  fi

  mkdir -p "$(dirname "$dst")"
  if [[ -d "$dst" ]]; then
    rm -rf "$dst"
  fi
  cp -r "$src" "$dst"
  echo "installed: $dst"
}

# ==============================================
# Skills → ~/.claude/skills/
# ==============================================
ALL_SKILLS=(
  agent-product-consultant
  agent-planner
  agent-worker
  agent-reviewer
  agent-integrator
  gh-fix-ci
)

echo "==> Skills (~/.claude/skills/)"
for skill in "${ALL_SKILLS[@]}"; do
  if [[ -d "$SKILLS_SRC/agents/$skill" ]]; then
    install_skill "$SKILLS_SRC/agents" "$SKILLS_DIR" "$skill"
  elif [[ -d "$SKILLS_SRC/tools/$skill" ]]; then
    install_skill "$SKILLS_SRC/tools" "$SKILLS_DIR" "$skill"
  else
    echo "warning: skill source not found for $skill" >&2
  fi
done

echo ""
echo "Skill installation complete."
echo "Installed skills will be available on the next Claude Code session."
