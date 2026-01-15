# Claude GitHub Skills

[中文版](#中文版) | [English](#english)

---

# 中文版

Claude Code 的 GitHub 工作流自动化技能集。从需求到合并 PR 的全生命周期自动化。

## 技能概览

| 技能 | 描述 | 触发命令 |
|------|------|----------|
| **gh-create-issue** | 从 PRD/需求创建结构化 Issue，自动评估复杂度 | `/gh-create-issue` |
| **gh-issue-implement** | 单个 Issue 实现：分析→开发→创建 PR | `/gh-issue-implement <number>` |
| **gh-pr-review** | 代码审查、修复问题、合并 PR | `/gh-pr-review <pr_number>` |
| **gh-project-sync** | 同步 Issue 到 GitHub Projects 看板 | `/gh-project-sync` |
| **gh-project-implement** | 并发实现 Project 中所有 Issue | `/gh-project-implement <project_number>` |
| **gh-project-pr** | Project 级别批量 PR 审查 | `/gh-project-pr <project_number>` |

## 工作流示意

```
PRD/需求文档
       │
       ▼
┌──────────────────┐
│ gh-create-issue  │  创建 Epic + 子任务
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ gh-project-sync  │  同步到 Project 看板
└────────┬─────────┘
         │
         ▼
┌───────────────────────┐
│ gh-project-implement  │  按优先级并发执行 (P0→P1→P2→P3)
│  └─ gh-issue-implement│  独立 worktree + Claude 会话
└────────┬──────────────┘
         │
         ▼
┌──────────────────┐
│  gh-project-pr   │  批量审查 → 合并 → 更新状态
│  └─ gh-pr-review │  单个 PR 审查
└──────────────────┘
```

## 核心特性

### 自适应并发执行

`gh-project-implement` 支持智能并发控制的并行 Issue 处理：

| 优先级 | 最大并发数 | 说明 |
|--------|------------|------|
| P0 | 4 | 紧急任务，高并发 |
| P1 | 3 | 中等优先级 |
| P2 | 2 | 普通任务 |
| P3 | 1 | 低优先级，节省资源 |

**依赖感知**：当 Issue 存在依赖关系时，并发数自动减 1 以避免过度等待。

### Project 级别 PR 审查

`gh-project-pr` 自动化整个 Project 的批量 PR 审查：

```bash
# 预览待审查的 PR（dry-run）
/gh-project-pr 1 --dry-run

# 执行批量审查并自动合并
/gh-project-pr 1 --auto-merge

# 按优先级过滤
/gh-project-pr 1 --priority p0,p1
```

**工作流阶段：**
1. **阶段 1-2**：获取 Project Items → 查找关联的 PR
2. **阶段 3**：按优先级排序 (P0→P1→P2→P3)
3. **阶段 4**：批量审查（默认串行，可选 `--parallel`）
4. **阶段 5**：更新 Project 状态为 "Done"
5. **阶段 6**：生成汇总报告

### 仓库级 Projects

所有 `gh-project-*` 技能默认支持**仓库级 Projects**：

```bash
# 默认：仓库级 Project
/gh-project-sync

# 回退：用户级 Project（向后兼容）
/gh-project-sync --user
```

## 安装

### 方式一：复制到 Claude Skills 目录

```bash
git clone https://github.com/wscffaa/claude-gh-skills.git
cp -r claude-gh-skills/gh-* ~/.claude/skills/
```

### 方式二：符号链接（开发用）

```bash
git clone https://github.com/wscffaa/claude-gh-skills.git
cd claude-gh-skills

for skill in gh-*; do
  ln -sf "$(pwd)/$skill" ~/.claude/skills/
done
```

## 依赖

- [Claude Code CLI](https://github.com/anthropics/claude-code) 已安装
- [GitHub CLI (gh)](https://cli.github.com/) 已安装并认证
- `gh` 权限：`repo`, `project`, `read:org`

## 快速开始

```bash
# 1. 从 PRD 创建 Issue
/gh-create-issue based on docs/my-feature-prd.md

# 2. 同步 Issue 到 Project 看板
/gh-project-sync

# 3. 并发实现 Project 中所有 Issue
/gh-project-implement 1

# 4. 批量审查 Project 中所有 PR
/gh-project-pr 1 --auto-merge

# 或单独处理 Issue/PR
/gh-issue-implement 42
/gh-pr-review 56
```

## 技能详情

### gh-create-issue

PM 级别任务拆分创建 GitHub Issue：
- 简单任务 → 单个 Issue
- 复杂任务 → Epic + 带依赖关系的子任务
- 自动分配优先级标签（`priority:p0` 到 `priority:p3`）

### gh-issue-implement

完整的 Issue 到 PR 生命周期：
1. 通过 `gh issue view` 获取 Issue 详情
2. 分析需求
3. 使用 dev 工作流实现
4. 创建带 "Closes #N" 引用的 PR

### gh-pr-review

全面的 PR 审查：
- 通过 codeagent 深度代码分析
- CI 状态验证
- 自动修复问题（最多 3 次迭代）
- Squash 合并并清理分支

### gh-project-sync

Project 看板集成：
- 创建或选择 GitHub Project（默认仓库级）
- 同步 Issue 到看板
- 按优先级自动分配状态列

### gh-project-implement

并发执行的批量 Project 实现：
- 获取 Project 中所有 Open Issue
- 按优先级分组（P0 → P1 → P2 → P3）
- 每批次内**并发执行**（DAG 调度器）
- 基于优先级和依赖关系的**自适应并发**
- 每个 Issue：独立 worktree + Claude 会话
- 失败重试（最多 3 次）

### gh-project-pr

Project 级别批量 PR 审查：
- **阶段 1**：获取 Project Items（过滤：Issue 类型，非 Done 状态）
- **阶段 2**：查找关联 PR（3 种策略：linked:issue、分支名、body 引用）
- **阶段 3**：按优先级排序
- **阶段 4**：通过 `gh-pr-review` 批量审查
- **阶段 5**：更新 Project Item 状态为 "Done"
- **阶段 6**：生成汇总报告

**CLI 选项：**

| 选项 | 说明 |
|------|------|
| `--dry-run` | 仅预览，不执行 |
| `--auto-merge` | 审批后自动合并 |
| `--parallel` | 并行审查（谨慎使用） |
| `--priority p0,p1` | 按优先级过滤 |
| `--user` | 使用用户级 Project |

## 相关仓库

- [claude-code-skills](https://github.com/wscffaa/claude-code-skills) - 更多 Claude Code 技能（AI 代码助手、规划文档、多代理编排等）

## 许可证

MIT

---

# English

Claude Code skills for GitHub workflow automation. Automate the full lifecycle from requirements to merged PRs.

## Skills Overview

| Skill | Description | Trigger |
|-------|-------------|---------|
| **gh-create-issue** | Create structured issues from PRD/requirements with auto complexity assessment | `/gh-create-issue` |
| **gh-issue-implement** | Implement single issue: analysis → dev → PR creation | `/gh-issue-implement <number>` |
| **gh-pr-review** | Code review, fix issues, merge PR | `/gh-pr-review <pr_number>` |
| **gh-project-sync** | Sync issues to GitHub Projects board | `/gh-project-sync` |
| **gh-project-implement** | Implement ALL issues in a Project with concurrent execution | `/gh-project-implement <project_number>` |
| **gh-project-pr** | Batch PR review for entire Project | `/gh-project-pr <project_number>` |

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

### Option 1: Copy to Claude Skills Directory

```bash
git clone https://github.com/wscffaa/claude-gh-skills.git
cp -r claude-gh-skills/gh-* ~/.claude/skills/
```

### Option 2: Symlink (for development)

```bash
git clone https://github.com/wscffaa/claude-gh-skills.git
cd claude-gh-skills

for skill in gh-*; do
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

## Related Repositories

- [claude-code-skills](https://github.com/wscffaa/claude-code-skills) - More Claude Code skills (AI code assistants, planning & docs, multi-agent orchestration, etc.)

## License

MIT
