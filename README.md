# Claude GitHub Skills

Claude Code skills for GitHub workflow automation. Automate the full lifecycle from requirements to merged PRs.

## Skills Overview

| Skill | Description | Trigger |
|-------|-------------|---------|
| **gh-create-issue** | Create structured issues from PRD/requirements with auto complexity assessment | `/gh-create-issue` |
| **gh-issue-implement** | Implement single issue: analysis → dev → PR creation | `/gh-issue-implement <number>` |
| **gh-issue-orchestrator** | List issues, analyze priorities/dependencies, batch implement | `/gh-issue-orchestrator` |
| **gh-pr-review** | Code review, fix issues, merge PR | `/gh-pr-review <pr_number>` |
| **gh-project-sync** | Sync issues to GitHub Projects board | `/gh-project-sync` |
| **gh-project-implement** | Implement ALL issues in a Project (batch by priority) | `/gh-project-implement <project_number>` |

## Workflow

```
PRD/Requirements
       │
       ▼
┌──────────────────┐
│ gh-create-issue  │  Create epic + sub-issues
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ gh-project-sync  │  Sync to Project board
└────────┬─────────┘
         │
         ▼
┌───────────────────────┐
│ gh-project-implement  │  Batch implement by priority (P0→P1→P2→P3)
│  ├─ gh-issue-implement│  Each issue: worktree + Claude session
│  └─ gh-pr-review      │  Review → Fix → Merge
└───────────────────────┘
```

## Installation

### Option 1: Copy to Claude Skills Directory

```bash
# Clone this repo
git clone https://github.com/wscffaa/claude-gh-skills.git

# Copy skills to your Claude skills directory
cp -r claude-gh-skills/skills/* ~/.claude/skills/
```

### Option 2: Symlink (for development)

```bash
git clone https://github.com/wscffaa/claude-gh-skills.git
cd claude-gh-skills

# Symlink each skill
for skill in skills/gh-*; do
  ln -sf "$(pwd)/$skill" ~/.claude/skills/
done
```

## Requirements

- [Claude Code CLI](https://github.com/anthropics/claude-code) installed
- [GitHub CLI (gh)](https://cli.github.com/) installed and authenticated
- `gh` permissions: `repo`, `project`, `read:org`

## Quick Start

```bash
# 1. Create issues from a PRD
/gh-create-issue based on docs/my-feature-prd.md

# 2. Sync issues to a Project board
/gh-project-sync

# 3. Implement all issues in the Project
/gh-project-implement 1

# Or implement a single issue
/gh-issue-implement 42

# Review and merge a PR
/gh-pr-review 56
```

## Skill Details

### gh-create-issue

Creates GitHub issues with PM-level task breakdown:
- Simple tasks → Single issue
- Complex tasks → Epic + sub-issues with dependencies
- Auto-assigns priority labels (`priority:p0` to `priority:p3`)

### gh-issue-implement

Full issue-to-PR lifecycle:
1. Fetch issue details via `gh issue view`
2. Analyze requirements
3. Implement using dev workflow
4. Create PR with "Closes #N" reference

### gh-issue-orchestrator

Batch orchestration:
- List all open issues
- Analyze priorities and dependencies
- Recommend implementation order
- Parallel execution with Git worktrees

### gh-pr-review

Comprehensive PR review:
- Deep code analysis via codeagent
- CI status verification
- Auto-fix issues (up to 3 iterations)
- Squash merge with branch cleanup

### gh-project-sync

Project board integration:
- Create or select GitHub Project
- Sync issues to board
- Auto-assign status columns by priority

### gh-project-implement

Batch Project implementation:
- Fetch all Open issues from Project
- Group by priority (P0 → P1 → P2 → P3)
- Each issue: isolated worktree + Claude session
- Immediate review and merge
- Retry on failure (max 3 times)

## License

MIT
