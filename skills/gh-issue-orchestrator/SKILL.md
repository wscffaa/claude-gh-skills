---
name: gh-issue-orchestrator
description: |
  GitHub Issue 工作流编排器。获取全部 issues，分析优先级和依赖关系，推荐实现顺序，支持批量调用 gh-issue-implement。
  触发条件：
  - 用户请求 "列出所有 issues" / "show all issues" / "issue 列表"
  - 用户请求 "下一个要做的 issue" / "next issue" / "推荐 issue"
  - 用户请求 "批量实现" / "batch implement" / "实现多个 issues"
  - 用户提到 "issue 优先级" / "依赖关系" / "实现顺序"
---

# GitHub Issue Orchestrator

编排 GitHub Issues 工作流：获取全部 issues，分析优先级和依赖关系，使用独立会话 + Git Worktree 并行实现。

## 斜杠命令

| 命令 | 说明 |
|------|------|
| `/gh-issues` | 列出所有 open issues，按优先级/依赖排序 |
| `/gh-issues next` | 推荐下一个要实现的 issue |
| `/gh-issues auto` | **全自动模式**：独立会话 + worktree 实现所有 issues |
| `/gh-issues auto N` | 全自动实现前 N 个 issues |

## 执行指令

### 命令解析

解析 `$ARGUMENTS` 参数：
- 无参数 → list 模式
- `next` → next 模式
- `auto` → auto 模式（全部）
- `auto N` → auto 模式（前 N 个）

### 模式 1: list（默认）

```bash
python3 .claude/skills/gh-issue-orchestrator/scripts/list_issues.py --mode list
```

直接输出结果给用户。

### 模式 2: next

```bash
python3 .claude/skills/gh-issue-orchestrator/scripts/list_issues.py --mode next
```

直接输出推荐结果。

### 模式 3: auto（全自动 + 独立会话 + Worktree）

**核心设计**：
- 每个 issue 在**独立 Claude 会话**中处理（避免上下文累积）
- 每个 issue 在**独立 Git Worktree**中开发（分支隔离）
- 串行执行确保依赖顺序

**执行步骤**：

1. **获取待实现 issues 列表**：
```bash
python3 .claude/skills/gh-issue-orchestrator/scripts/list_issues.py --mode auto [--count N]
# 输出: [11, 12, 13, ...]
```

2. **对每个 issue 依次执行**：

```bash
# Step A: 创建 Git Worktree（独立工作目录）
WORKTREE_PATH=$(python3 .claude/skills/gh-issue-orchestrator/scripts/worktree.py create {issue_number})
# 输出: /path/to/repo-worktrees/issue-11

# Step B: 在 worktree 目录启动独立 Claude 会话实现 issue
cd "$WORKTREE_PATH" && claude -p "/gh-issue-implement {issue_number}"

# Step C: 获取新创建的 PR 编号
PR_NUMBER=$(gh pr list --author @me --head issue-{issue_number} --json number -q '.[0].number')

# Step D: 启动独立 Claude 会话 Review PR
claude -p "/gh-pr-review $PR_NUMBER"

# Step E: 清理 worktree（PR 合并后）
python3 .claude/skills/gh-issue-orchestrator/scripts/worktree.py remove {issue_number}
```

3. **输出总结报告**：
```
## 自动化完成报告

| Issue | Branch | PR | 状态 |
|-------|--------|-----|------|
| #11 | issue-11 | #25 | ✅ Merged |
| #12 | issue-12 | #26 | ✅ Merged |
| #13 | issue-13 | #27 | ✅ Merged |

共完成 3 个 issues，全部已合并。
Worktrees 已清理。
```

## Git Worktree 管理

```bash
# 创建 worktree（返回路径）
python3 scripts/worktree.py create 11
# → /path/to/BasicOFR-worktrees/issue-11

# 删除 worktree
python3 scripts/worktree.py remove 11

# 列出所有 issue worktrees
python3 scripts/worktree.py list

# 清理已合并的 worktrees
python3 scripts/worktree.py cleanup

# 获取 worktree 路径
python3 scripts/worktree.py path 11
```

**Worktree 目录结构**：
```
/path/to/
├── BasicOFR/                    # 主仓库（main 分支）
└── BasicOFR-worktrees/          # worktree 目录
    ├── issue-11/                # issue #11 的独立工作目录
    ├── issue-12/                # issue #12 的独立工作目录
    └── issue-13/                # issue #13 的独立工作目录
```

## 关键：独立会话模式

**每个 issue 使用 `claude -p` 启动独立会话**：

```bash
# 在 worktree 目录中启动独立 Claude 会话
cd /path/to/BasicOFR-worktrees/issue-11
claude -p "/gh-issue-implement 11"
```

**优势**：
- 上下文隔离：每个 issue 独立处理，不会累积
- 分支隔离：每个 issue 在独立 worktree 开发
- 可并行：不同 issue 可同时在不同终端处理

## 自动模式完整流程

```python
# 伪代码展示完整流程
issues = get_issues_list()  # [11, 12, 13]

for issue_number in issues:
    # 1. 创建 worktree
    worktree_path = run(f"python3 scripts/worktree.py create {issue_number}")

    # 2. 在 worktree 中实现 issue（独立会话）
    run(f"cd {worktree_path} && claude -p '/gh-issue-implement {issue_number}'")

    # 3. 获取 PR 编号
    pr_number = run(f"gh pr list --head issue-{issue_number} --json number -q '.[0].number'")

    # 4. Review PR（独立会话）
    run(f"claude -p '/gh-pr-review {pr_number}'")

    # 5. 清理 worktree
    run(f"python3 scripts/worktree.py remove {issue_number}")

print("所有 issues 处理完成")
```

## 优先级/依赖解析

**优先级**（从 labels）：
- `priority:p0` → Critical（最高）
- `priority:p1` → High
- `priority:p2` → Medium
- `priority:p3` → Low

**依赖**（从 body）：
- `Depends on #N` / `依赖 #N`
- `Blocked by #N`
- `Part of #N`

## 示例交互

```
User: /gh-issues
Claude: [显示所有 issues 列表]

User: /gh-issues next
Claude: [推荐下一个 issue]

User: /gh-issues auto 3
Claude:
正在处理 3 个 issues...

[Issue #11]
→ 创建 worktree: /path/to/BasicOFR-worktrees/issue-11
→ 启动独立会话: claude -p "/gh-issue-implement 11"
→ PR #25 已创建
→ 启动 Review: claude -p "/gh-pr-review 25"
→ PR #25 已合并
→ 清理 worktree

[Issue #12]
→ 创建 worktree: /path/to/BasicOFR-worktrees/issue-12
→ 启动独立会话: claude -p "/gh-issue-implement 12"
...

## 自动化完成报告
| Issue | PR | 状态 |
|-------|-----|------|
| #11 | #25 | ✅ Merged |
| #12 | #26 | ✅ Merged |
| #13 | #27 | ✅ Merged |
```
