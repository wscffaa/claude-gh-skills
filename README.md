# Claude GitHub Skills

Claude Code skills for GitHub workflow automation. Automate the full lifecycle from requirements to merged PRs.

## Skills Overview

| Skill | Description | Trigger |
|-------|-------------|---------|
| **gh-create-issue** | Create structured issues from PRD/requirements with auto complexity assessment | `/gh-create-issue` |
| **gh-issue-implement** | Implement single issue: analysis → dev → PR creation | `/gh-issue-implement <number>` |
| **gh-pr-review** | Code review, fix issues, merge PR | `/gh-pr-review <pr_number>` |
| **gh-project-sync** | Sync issues to GitHub Projects board | `/gh-project-sync` |
| **gh-project-implement** | Implement ALL issues in a Project with concurrent execution | `/gh-project-implement <project_number>` |

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
│ gh-project-implement  │  Concurrent batch by priority (P0→P1→P2→P3)
│  ├─ gh-issue-implement│  Parallel worktrees + Claude sessions
│  └─ gh-pr-review      │  Review → Fix → Merge
└───────────────────────┘
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

### DAG Scheduler

- **Dependency tracking**: Issues only start when dependencies complete
- **Blocked detection**: Issues with failed dependencies are auto-skipped
- **Thread-safe**: Concurrent execution with proper locking

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

# 3. Implement all issues in the Project (concurrent)
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

Batch Project implementation with concurrent execution:
- Fetch all Open issues from Project
- Group by priority (P0 → P1 → P2 → P3)
- **Concurrent execution** within each batch (DAG scheduler)
- **Adaptive parallelism** based on priority and dependencies
- Each issue: isolated worktree + Claude session
- Immediate review and merge
- Retry on failure (max 3 times)
- Built-in worktree management

**Example output:**
```
🚀 开始处理 (共 10 个 issues)

📦 P0 批次 (2 issues, 并发=4)
[1/10] 正在处理 Issue #42: 添加登录功能 (P0)
[2/10] 正在处理 Issue #43: 修复 bug (P0)
✅ Issue #43 已完成，PR #57 已合并 (耗时 1m15s)
✅ Issue #42 已完成，PR #56 已合并 (耗时 2m30s)
📦 P0 批次完成 (2/2)

📦 P1 批次 (3 issues, 并发=2)
...
```

## License

MIT
