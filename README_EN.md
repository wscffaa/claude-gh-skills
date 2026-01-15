# Claude GitHub Skills

[中文](README.md)

Claude Code skills for GitHub workflow automation. Automate the full lifecycle from requirements to merged PRs.

## Skills Overview

### Requirements & Issue Creation

| Skill | Description | Trigger |
|-------|-------------|---------|
| **product-requirements** | Interactive requirements gathering & PRD generation with 100-point quality scoring | `/product-requirements` |
| **gh-create-issue** | Create structured issues from PRD/requirements with auto complexity assessment | `/gh-create-issue` |

### Single Issue/PR Processing

| Skill | Description | Trigger |
|-------|-------------|---------|
| **gh-issue-implement** | Implement single issue: analysis → dev → PR creation | `/gh-issue-implement <number>` |
| **gh-pr-review** | Code review, fix issues, merge PR | `/gh-pr-review <pr_number>` |

### Project-Level Batch Processing

| Skill | Description | Trigger |
|-------|-------------|---------|
| **gh-project-sync** | Sync issues to GitHub Projects board | `/gh-project-sync` |
| **gh-project-implement** | Implement ALL issues in a Project with concurrent execution | `/gh-project-implement <project_number>` |
| **gh-project-pr** | Batch PR review for entire Project | `/gh-project-pr <project_number>` |

## Workflow

### Single Issue/PR Processing

```
User Requirements
       │
       ▼
┌───────────────────────┐
│ product-requirements  │  Interactive gathering → Generate PRD
└────────┬──────────────┘
         │
         ▼
┌──────────────────┐
│ gh-create-issue  │  Create single issue
└────────┬─────────┘
         │
         ▼
┌───────────────────────┐
│  gh-issue-implement   │  Analyze → Develop → Create PR
└────────┬──────────────┘
         │
         ▼
┌──────────────────┐
│   gh-pr-review   │  Review → Fix → Merge
└──────────────────┘
```

### Project-Level Batch Processing

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
│ gh-project-implement  │  Concurrent batch by priority (P0→P1→P2→P3)
│  └─ gh-issue-implement│  Parallel worktrees + Claude sessions
└────────┬──────────────┘
         │
         ▼
┌──────────────────┐
│  gh-project-pr   │  Batch review → merge → update status
│  └─ gh-pr-review │  Per-PR review
└──────────────────┘
```

## Key Features

### Concurrent Execution with Adaptive Parallelism

`gh-project-implement` supports parallel issue processing with intelligent concurrency control:

| Priority | Max Workers | Description |
|----------|-------------|-------------|
| P0 | 4 | Urgent tasks, high parallelism |
| P1 | 3 | Medium priority |
| P2 | 2 | Normal tasks |
| P3 | 1 | Low priority, conserve resources |

**Dependency-aware**: When issues have dependencies, parallelism is reduced by 1 to avoid excessive waiting.

### Project-Level PR Review

`gh-project-pr` automates batch PR review for an entire Project:

```bash
# Preview PRs to review (dry-run)
/gh-project-pr 1 --dry-run

# Execute batch review with auto-merge
/gh-project-pr 1 --auto-merge

# Filter by priority
/gh-project-pr 1 --priority p0,p1
```

**Workflow Phases:**
1. **Phase 1-2**: Get Project Items → Find linked PRs
2. **Phase 3**: Sort by priority (P0→P1→P2→P3)
3. **Phase 4**: Batch review (serial by default, `--parallel` optional)
4. **Phase 5**: Update Project status to "Done"
5. **Phase 6**: Generate summary report

### Repository-Level Projects

All `gh-project-*` skills now support **repository-level Projects** by default:

```bash
# Default: repository-level Project
/gh-project-sync

# Fallback: user-level Project (backward compatible)
/gh-project-sync --user
```

## Installation

### First-time Install

```bash
# Clone to a fixed directory
git clone https://github.com/wscffaa/claude-gh-skills.git ~/claude-gh-skills

# Symlink to Claude Skills directory
for skill in ~/claude-gh-skills/gh-*; do
  ln -sf "$skill" ~/.claude/skills/
done
```

### Update Skills

```bash
cd ~/claude-gh-skills && git pull
```

> With symlinks, skills update instantly after `git pull` - no need to re-copy.

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

# 3. Implement all issues in the Project (concurrent)
/gh-project-implement 1

# 4. Batch review all PRs in the Project
/gh-project-pr 1 --auto-merge

# Or work with individual issues/PRs
/gh-issue-implement 42
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

### gh-pr-review

Comprehensive PR review:
- Deep code analysis via codeagent
- CI status verification
- Auto-fix issues (up to 3 iterations)
- Squash merge with branch cleanup

### gh-project-sync

Project board integration:
- Create or select GitHub Project (repository-level by default)
- Sync issues to board
- Auto-assign status columns by priority

### gh-project-implement

Batch Project implementation with concurrent execution:
- Fetch all Open issues from Project
- Group by priority (P0 → P1 → P2 → P3)
- **Concurrent execution** within each batch (DAG scheduler)
- **Adaptive parallelism** based on priority and dependencies
- Each issue: isolated worktree + Claude session
- Retry on failure (max 3 times)

### gh-project-pr

Batch PR review for entire Project:
- **Phase 1**: Get Project Items (filter: Issue type, non-Done status)
- **Phase 2**: Find linked PRs (3 strategies: linked:issue, branch name, body reference)
- **Phase 3**: Sort by priority
- **Phase 4**: Batch review via `gh-pr-review`
- **Phase 5**: Update Project Item status to "Done"
- **Phase 6**: Generate summary report

**CLI Options:**

| Option | Description |
|--------|-------------|
| `--dry-run` | Preview only, no execution |
| `--auto-merge` | Auto-merge after approval |
| `--parallel` | Parallel review (use with caution) |
| `--priority p0,p1` | Filter by priority |
| `--user` | Use user-level Project |

## Credits

- **product-requirements** skill is adapted from [cexll/myclaude](https://github.com/cexll/myclaude)

## License

MIT
