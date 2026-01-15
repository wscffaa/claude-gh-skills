---
name: gh-project-implement
description: 按优先级并发实现 Project 中所有 Issue。
---

# gh-project-implement

自动化实现 GitHub Project 下所有 Open Issues，一键完成整个 Sprint。

## 斜杠命令

| 命令 | 说明 |
|------|------|
| `/gh-project-implement <number>` | 实现指定 Project 下所有 Open Issues |
| `/gh-project-implement <number> --user` | 使用用户级 Project（向后兼容） |
| `/gh-project-implement <number> --max-retries 5` | 指定最大重试次数 |
| `/gh-project-implement <number> --yes` | 跳过确认直接执行 |

## 核心功能

1. **Project Issues 获取** - 获取指定 Project 下所有 Open Issues（默认仓库级）
2. **优先级分批执行** - 按 P0 → P1 → P2 → P3 分批，每批内按依赖排序
3. **并发执行** - 批次内依赖感知的 DAG 并发调度
4. **自适应并发数** - 根据优先级和依赖关系动态调整并发数
5. **Worktree 隔离** - 每个 issue 使用独立 worktree + Claude 会话
6. **即时合并** - 实现 → Review → 合并 → 下一个
7. **失败重试** - 失败立即重试，最多 N 次（默认 3）
8. **进度追踪** - 控制台实时进度 + 完成报告

## 自适应并发数

根据优先级和依赖关系动态计算并发数：

| 优先级 | 基础并发数 | 说明 |
|--------|-----------|------|
| P0 | 4 | 紧急任务，高并发 |
| P1 | 3 | 中等优先级 |
| P2 | 2 | 一般任务 |
| P3 | 1 | 低优先级，节省资源 |

**依赖调整**：批次内存在依赖关系时，并发数 -1（避免过多等待）

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

输出格式（包含依赖信息）：
```json
{
  "batches": [
    {
      "priority": "p0",
      "issues": [
        {"number": 42, "title": "xxx", "dependencies": []},
        {"number": 43, "title": "yyy", "dependencies": [42]}
      ]
    }
  ]
}
```

### Phase 3: 并发批量执行

```bash
python3 scripts/batch_executor.py --input <batcher_output.json> --max-retries 3
```

对每个批次并发执行（DAG 调度）：
1. 计算自适应并发数
2. 获取可执行的 issues（依赖已完成）
3. 并发创建 worktree 并启动 Claude 会话
4. 完成后立即 Review + Merge
5. 等待所有任务完成后进入下一批次

## 脚本

### get_project_issues.py

获取 Project 下所有 Open Issues。**默认获取仓库级 Project**。

```bash
# 默认仓库级 Project
python3 scripts/get_project_issues.py --project 1 --json

# 指定 owner（仓库级）
python3 scripts/get_project_issues.py --project 1 --owner wscffaa --json

# 使用用户级 Project（向后兼容）
python3 scripts/get_project_issues.py --project 1 --user --json
```

### priority_batcher.py

按优先级分批并按依赖排序，输出包含依赖信息。

```bash
cat issues.json | python3 scripts/priority_batcher.py --json
python3 scripts/priority_batcher.py --input issues.json --json
```

### batch_executor.py

并发批量执行引擎。

```bash
cat batches.json | python3 scripts/batch_executor.py
python3 scripts/batch_executor.py --input batches.json --max-retries 5
python3 scripts/batch_executor.py --input batches.json --max-workers 2  # 覆盖自适应并发数
```

## 输出示例

```
🚀 开始处理 (共 10 个 issues)

📦 P0 批次 (2 issues, 并发=4)
[1/10] 正在处理 Issue #42: 添加登录功能 (P0)
[2/10] 正在处理 Issue #43: 修复 bug (P0)
✅ Issue #43 已完成，PR #57 已合并 (耗时 1m15s)
✅ Issue #42 已完成，PR #56 已合并 (耗时 2m30s)
📦 P0 批次完成 (2/2)

📦 P1 批次 (3 issues, 并发=2)
[3/10] 正在处理 Issue #44: 添加测试 (P1)
[4/10] 正在处理 Issue #45: 重构代码 (P1)
🔄 Issue #44 第 1/3 次重试...
✅ Issue #45 已完成，PR #59 已合并 (耗时 3m10s)
[5/10] 正在处理 Issue #46: 更新文档 (P1)
✅ Issue #44 已完成，PR #58 已合并 (耗时 5m20s)
...

## 完成报告

| Issue | Title | PR | Status | Time |
|-------|-------|-----|--------|------|
| #42 | 添加登录功能 | #56 | completed | 2m30s |
| #43 | 修复 bug | #57 | completed | 1m15s |
| #44 | 添加测试 | #58 | completed | 5m20s |
...

总计: 10 issues, 9 成功, 1 失败
总耗时: 15m30s (并发加速)
```

## 技术约束

- 需要 gh CLI 2.0+ 并已认证
- 需要 `project` scope 权限
- 依赖 `gh-issue-implement` 和 `gh-pr-review` skills

## 目录结构

```
.claude/skills/gh-project-implement/
├── SKILL.md              # 本文件
└── scripts/
    ├── get_project_issues.py   # 获取 Project Issues
    ├── priority_batcher.py     # 优先级分批（含依赖信息）
    ├── batch_executor.py       # 并发批量执行引擎
    ├── status_sync.py          # Project 状态同步
    └── worktree.py             # Git Worktree 管理
```

## 参考

- PRD: `docs/gh-project-implement-prd.md`
- Epic: #91
