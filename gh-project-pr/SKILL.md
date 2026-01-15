---
name: gh-project-pr
description: |
  获取 GitHub Project 中 Issues 与 PR 的映射关系。
  支持多种 PR 查找策略：linked:issue、分支名、body 引用。
  触发条件：
  - /gh-project-pr <project_number>
  - 用户提到"project pr"、"issue pr 映射"
---

# gh-project-pr

获取 GitHub Project 中 Issues 与 PR 的映射关系，用于追踪实现进度。

## 斜杠命令

| 命令 | 说明 |
|------|------|
| `/gh-project-pr <number>` | 获取指定 Project 的 Issue-PR 映射 |
| `/gh-project-pr <number> --dry-run` | 预览模式：只显示待审查 PR，不执行审查 |
| `/gh-project-pr <number> --json` | JSON 格式输出 |
| `/gh-project-pr <number> --user` | 用户级 Project |

## 核心功能

### Phase 1: 获取 Project Items

```bash
gh project item-list <number> --owner <owner> --format json
```

过滤条件：
- 类型为 Issue（排除 PR、Draft Issue）
- 状态非 Done

### Phase 2: 查找关联 PR

对每个 Issue 依次尝试 3 种方式：

1. **linked:issue 搜索**
   ```bash
   gh pr list --search "linked:issue:<N>"
   ```

2. **分支名匹配**
   ```bash
   gh pr list --head "feat/issue-<N>"
   # 还会尝试: feature/issue-<N>, fix/issue-<N>, issue-<N>
   ```

3. **Body 引用搜索**
   ```bash
   gh pr list --search "Closes #<N> in:body"
   # 还会尝试: Fixes #<N>, Resolves #<N>
   ```

## 用法

### 主入口 (main.py)

```bash
# 预览模式（只执行 Phase 1-3，显示待审查 PR）
python3 scripts/main.py --project 1 --dry-run

# 指定 owner
python3 scripts/main.py --project 1 --owner wscffaa --dry-run

# JSON 输出
python3 scripts/main.py --project 1 --dry-run --json

# 按优先级过滤
python3 scripts/main.py --project 1 --dry-run --priority p0,p1
```

### dry-run 输出示例

```
Found 4 PRs to review:

| Issue | PR | Priority | Title |
|-------|-----|----------|-------|
| #108 | #112 | P0 | feat: implement analyzer |
| #109 | #113 | P0 | feat: implement codegen |
| #110 | #114 | P1 | deprecate old skills |
| #111 | - | P1 | (no PR) |

Run without --dry-run to execute review.
```

### 单独脚本

```bash
# 基本用法
python3 scripts/get_project_prs.py --project 1

# JSON 输出
python3 scripts/get_project_prs.py --project 1 --json

# 指定 owner
python3 scripts/get_project_prs.py --project 1 --owner wscffaa --json

# 用户级 Project（与默认行为相同）
python3 scripts/get_project_prs.py --project 1 --user --json
```

## 输出格式

### JSON 格式 (--json)

```json
{
  "mappings": [
    {
      "issue": 108,
      "pr": 112,
      "title": "实现用户认证",
      "pr_title": "feat: add user authentication",
      "state": "open",
      "priority": "p0"
    },
    {
      "issue": 109,
      "pr": null,
      "title": "添加单元测试",
      "state": null,
      "priority": "p1"
    }
  ],
  "stats": {
    "total_issues": 4,
    "with_pr": 3,
    "without_pr": 1,
    "pr_open": 2,
    "pr_merged": 1,
    "pr_closed": 0
  }
}
```

### 文本格式（默认）

```
============================================================
Project #1 Issue-PR 映射
============================================================

统计:
  总 Issues: 4
  有 PR: 3
  无 PR: 1
  PR Open: 2
  PR Merged: 1
  PR Closed: 0

映射列表:
------------------------------------------------------------
  Issue # 108 -> PR # 112 (O) [p0] 实现用户认证
  Issue # 109 -> (no PR)      [p1] 添加单元测试
  Issue # 110 -> PR # 115 (M) [p2] 修复登录 bug
  Issue # 111 -> PR # 116 (O) [--] 更新文档
```

状态标识：
- `O` = Open
- `M` = Merged
- `X` = Closed

## 典型用例

### 1. 追踪 Sprint 进度

```bash
python3 scripts/get_project_prs.py --project 1 --json | jq '.stats'
```

### 2. 查找无 PR 的 Issues

```bash
python3 scripts/get_project_prs.py --project 1 --json | \
  jq '.mappings[] | select(.pr == null) | .issue'
```

### 3. 查找待合并的 PR

```bash
python3 scripts/get_project_prs.py --project 1 --json | \
  jq '.mappings[] | select(.state == "open") | {issue, pr}'
```

## 技术约束

- 需要 gh CLI 2.0+ 并已认证
- 需要 `project` 和 `repo` scope 权限
- 大型 Project 可能需要较长执行时间（每个 Issue 最多 3 次 API 调用）

## 目录结构

```
.claude/skills/gh-project-pr/
├── SKILL.md                       # 本文件
└── scripts/
    ├── main.py                    # 主入口（支持 --dry-run）
    ├── get_project_prs.py         # Phase 1-2: 获取 Items 并查找 PR
    ├── sort_by_priority.py        # Phase 3: 按优先级排序
    ├── batch_review.py            # Phase 4: 批量审查 PR
    ├── update_status.py           # Phase 5: 更新 Project 状态
    └── generate_report.py         # Phase 6: 生成报告
```

## 相关 Skills

- `gh-project-implement`: 批量实现 Project Issues
- `gh-project-sync`: 同步 Issues 到 Project
