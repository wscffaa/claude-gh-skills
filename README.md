# Claude GitHub Skills

[English](README_EN.md)

Claude Code 的 GitHub 工作流自动化技能集。从需求到合并 PR 的全生命周期自动化。

## 技能概览

### 一键自动化（推荐）

| 技能 | 描述 | 触发命令 |
|------|------|----------|
| **gh-autopilot** | 端到端自动化：PRD→Issue→Project→实现→PR→合并 | `/gh-autopilot [PRD或需求]` |

### 需求与 Issue 创建

| 技能 | 描述 | 触发命令 |
|------|------|----------|
| **product-requirements** | 交互式需求收集与 PRD 生成，100 分质量评估体系 | `/product-requirements` |
| **gh-create-issue** | 从 PRD/需求创建结构化 Issue，自动评估复杂度 | `/gh-create-issue` |

### 单个 Issue/PR 处理

| 技能 | 描述 | 触发命令 |
|------|------|----------|
| **gh-issue-implement** | 单个 Issue 实现：分析→开发→创建 PR | `/gh-issue-implement <number>` |
| **gh-pr-review** | 代码审查、修复问题、合并 PR | `/gh-pr-review <pr_number>` |

### Project 级别批量处理

| 技能 | 描述 | 触发命令 |
|------|------|----------|
| **gh-project-sync** | 同步 Issue 到 GitHub Projects 看板 | `/gh-project-sync` |
| **gh-project-implement** | 并发实现 Project 中所有 Issue | `/gh-project-implement <project_number>` |
| **gh-project-pr** | Project 级别批量 PR 审查 | `/gh-project-pr <project_number>` |

## 工作流示意

### gh-autopilot 一键自动化（推荐）

```
用户需求/PRD
       │
       ▼
┌──────────────────────────────────────────────────────┐
│                    gh-autopilot                      │
│  ┌────────────────────────────────────────────────┐  │
│  │ 1. product-requirements  → 生成 PRD（可选）    │  │
│  │ 2. gh-create-issue       → 创建 Issue         │  │
│  │ 3. gh-project-sync       → 同步看板           │  │
│  │ 4. gh-project-implement  → 并发实现           │  │
│  │ 5. gh-project-pr         → 审查合并           │  │
│  └────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
       │
       ▼
    完成报告
```

### 单个 Issue/PR 处理

```
用户需求
       │
       ▼
┌───────────────────────┐
│ product-requirements  │  交互式需求收集 → 生成 PRD
└────────┬──────────────┘
         │
         ▼
┌──────────────────┐
│ gh-create-issue  │  创建单个 Issue
└────────┬─────────┘
         │
         ▼
┌───────────────────────┐
│  gh-issue-implement   │  分析 → 开发 → 创建 PR
└────────┬──────────────┘
         │
         ▼
┌──────────────────┐
│   gh-pr-review   │  审查 → 修复 → 合并
└──────────────────┘
```

### Project 级别批量处理

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

### 首次安装

```bash
# 克隆到固定目录
git clone https://github.com/wscffaa/claude-gh-skills.git ~/claude-gh-skills

# 符号链接到 Claude Skills 目录
for skill in ~/claude-gh-skills/gh-*; do
  ln -sf "$skill" ~/.claude/skills/
done
```

### 更新技能

```bash
cd ~/claude-gh-skills && git pull
```

> 使用符号链接方式，`git pull` 后技能即时生效，无需重新复制。

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

## 致谢

- **product-requirements** 技能源自 [cexll/myclaude](https://github.com/cexll/myclaude)

## 许可证

MIT
