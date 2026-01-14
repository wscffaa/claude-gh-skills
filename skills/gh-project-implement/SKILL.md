---
name: gh-project-implement
description: |
  自动化实现 GitHub Project 下所有 Open Issues。按优先级分批（P0 → P1 → P2 → P3），
  每个 issue 使用独立 worktree + Claude 会话，支持即时 Review/合并和失败重试。
  触发条件：
  - /gh-project-implement <project_number>
  - 用户提到"实现项目"、"批量实现"、"project implement"
---

# gh-project-implement

自动化实现 GitHub Project 下所有 Open Issues，一键完成整个 Sprint。

## 斜杠命令

| 命令 | 说明 |
|------|------|
| `/gh-project-implement <number>` | 实现指定 Project 下所有 Open Issues |
| `/gh-project-implement <number> --max-retries 5` | 指定最大重试次数 |
| `/gh-project-implement <number> --yes` | 跳过确认直接执行 |

## 核心功能

1. **Project Issues 获取** - 获取指定 Project 下所有 Open Issues
2. **优先级分批执行** - 按 P0 → P1 → P2 → P3 分批，每批内按依赖排序
3. **Worktree 隔离** - 每个 issue 使用独立 worktree + Claude 会话
4. **即时合并** - 实现 → Review → 合并 → 下一个
5. **失败重试** - 失败立即重试，最多 N 次（默认 3）
6. **进度追踪** - 控制台实时进度 + 完成报告

## 工作流程

### Phase 1: 获取 Project Issues

```bash
python3 scripts/get_project_issues.py --project <number> --json
```

输出 Open 状态的 Issues，过滤掉已有 PR 的 Issues。

### Phase 2: 优先级分批

```bash
python3 scripts/get_project_issues.py --project <number> --json | \
python3 scripts/priority_batcher.py --json
```

按 P0 → P1 → P2 → P3 分批，每批内按依赖关系拓扑排序。

### Phase 3: 批量执行

```bash
python3 scripts/batch_executor.py --input <batcher_output.json> --max-retries 3
```

对每个 issue：
1. 创建 worktree: `{repo}-worktrees/issue-{number}`
2. 启动独立会话: `claude -p "/gh-issue-implement {number}"`
3. 获取 PR 编号: `gh pr list --head issue-{number}`
4. Review PR: `claude -p "/gh-pr-review {pr_number}"`
5. 合并 PR: `gh pr merge {pr_number} --squash --delete-branch`
6. 清理 worktree

失败时自动重试（最多 N 次），重试前清理 worktree 和远程分支。

## 脚本

### get_project_issues.py

获取 Project 下所有 Open Issues。

```bash
python3 scripts/get_project_issues.py --project 1 --json
python3 scripts/get_project_issues.py --project 1 --owner wscffaa --json
```

### priority_batcher.py

按优先级分批并按依赖排序。

```bash
cat issues.json | python3 scripts/priority_batcher.py --json
python3 scripts/priority_batcher.py --input issues.json --json
```

### batch_executor.py

批量执行引擎。

```bash
cat batches.json | python3 scripts/batch_executor.py
python3 scripts/batch_executor.py --input batches.json --max-retries 5
```

## 输出示例

```
🚀 开始处理 (共 10 个 issues)

📦 P0 批次 (2 issues)
[1/10] 正在处理 Issue #42: 添加登录功能 (P0)
✅ Issue #42 已完成，PR #56 已合并 (耗时 2m30s)
[2/10] 正在处理 Issue #43: 修复 bug (P0)
✅ Issue #43 已完成，PR #57 已合并 (耗时 1m15s)
📦 P0 批次完成 (2/2)

📦 P1 批次 (3 issues)
[3/10] 正在处理 Issue #44: 添加测试 (P1)
🔄 Issue #44 第 1/3 次重试...
✅ Issue #44 已完成，PR #58 已合并 (耗时 5m20s)
...

## 完成报告

| Issue | Title | PR | Status | Time |
|-------|-------|-----|--------|------|
| #42 | 添加登录功能 | #56 | ✅ Merged | 2m30s |
| #43 | 修复 bug | #57 | ✅ Merged | 1m15s |
| #44 | 添加测试 | #58 | ✅ Merged | 5m20s |
...

总计: 10 issues, 9 成功, 1 失败
总耗时: 25m30s
```

## 技术约束

- 需要 gh CLI 2.0+ 并已认证
- 需要 `project` scope 权限
- 依赖 `gh-issue-implement` 和 `gh-pr-review` skills
- 依赖 `gh-issue-orchestrator/worktree.py` 脚本

## 目录结构

```
.claude/skills/gh-project-implement/
├── SKILL.md              # 本文件
└── scripts/
    ├── get_project_issues.py   # 获取 Project Issues
    ├── priority_batcher.py     # 优先级分批
    └── batch_executor.py       # 批量执行引擎
```

## 参考

- PRD: `docs/gh-project-implement-prd.md`
- Epic: #91
