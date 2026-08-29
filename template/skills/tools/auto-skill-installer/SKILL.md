---
name: auto-skill-installer
description: Use when the user wants Claude Code to find and install a skill from a natural-language description rather than a known skill name. Search GitHub and skills.sh for likely matches, install the best fit into .claude/skills/, then verify.
---

# Auto Skill Installer

Find, choose, install, and verify a Skill from a user description. This skill is for discovery plus installation, not just raw listing.

## When To Use

Use this skill when the user says things like:

- "帮我找一个能做 X 的 skill"
- "根据这个需求安装合适的 skill"
- "去 GitHub 或 skills.sh 找个能处理 Y 的 skill"

Do not use this skill when the user already gave an exact local skill name that can be installed directly without search.

## Outcome

By the end of the workflow you should:

1. Search for candidate skills on GitHub and skills.sh.
2. Pick the best match and explain why it fits.
3. Install it from GitHub into `.claude/skills/`.
4. Verify the installed directory contains a valid `SKILL.md`.
5. Tell the user whether a restart is needed and give exact follow-up commands they can say.

## Search Workflow

### 1. Turn the request into search terms

Extract:

- the task domain
- required tools or platforms
- hard constraints
- nice-to-have features

Produce 2 to 4 short search queries, for example:

- `<task> claude code skill`
- `<task> agent skill`
- `site:skills.sh <task>`
- `site:github.com SKILL.md <task>`

### 2. Search both sources

Use web search for discovery:

- Search skills.sh first when the request is broad.
- Search GitHub directly when the request is technical or niche.

When a result is on GitHub:

- Confirm there is an actual skill directory, not just a mention.
- Prefer repositories that contain a `SKILL.md` in the candidate path.
- Prefer clear install paths such as `skills/<name>` or a top-level skill directory.

### 3. Rank candidates

Rank by:

1. Direct match to the user's description.
2. Presence of `SKILL.md`.
3. Clear repo/path installability.
4. Signs the skill is maintained and documented.
5. Low ambiguity about what the skill does.

Reject candidates that are only blog posts, prompts, or README mentions without a real skill folder.

## Installation Workflow

Preferred install method:

1. If you have a GitHub tree URL, use `npx skills add <url>`.
2. If you have repo plus path, use `npx skills add <owner/repo@skill>`.

If multiple candidates are close, pick the smallest reasonable set and explain the tradeoff. Default to installing one skill unless the user explicitly wants multiple.

## Verification Workflow

After installation, always verify locally.

### Required checks

1. Confirm the skill's `SKILL.md` exists in `.claude/skills/<name>/` or the global skills directory.
2. Read the frontmatter `name` and `description`.
3. Confirm the skill is loadable.

If verification fails, say exactly what is missing and do not claim the skill is ready.

## What To Tell The User

After a successful install:

1. Name the installed skill and where it was installed from.
2. State whether a restart is needed: `Restart Claude Code to pick up new skills.`
3. Give exact commands or prompts the user can use next: `/<skill-name>` or describe the trigger phrase.

## Response Style

Keep the final response short and operational:

- what you found
- what you installed
- verification result
- exact next commands

If no safe, installable skill is found, say that clearly and include the best search result you found plus why it was rejected.
