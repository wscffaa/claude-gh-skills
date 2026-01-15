---
name: gh-project-sync
description: 同步 Issue 到 GitHub Projects 看板。
---

# gh-project-sync

将 GitHub Issues 同步到 GitHub Projects，实现可视化任务管理。

## 斜杠命令

| 命令 | 说明 |
|------|------|
| `/gh-project-sync` | 交互式同步 Issues 到 Project |
| `/gh-project-sync #63-#71` | 同步指定范围的 Issues |
| `/gh-project-sync --all` | 同步所有未加入 Project 的 Open Issues |

## 工作流程

### Phase 1: 项目选择

1. **获取项目列表**：
```bash
python3 .claude/skills/gh-project-sync/scripts/list_projects.py --json
```

2. **交互选择**：使用 `AskUserQuestion` 展示选项
   - 已有项目列表（显示名称和 Project #）
   - 新建项目
   - 跳过

3. **处理选择**：
   - 已有项目 → 获取 Project ID，进入 Phase 2
   - 新建项目 → 进入新建流程（Story 4）
   - 跳过 → 结束

### Phase 2: 范围选择

使用 `AskUserQuestion` 让用户选择同步范围：

1. **本次创建的 Issues**：从上下文获取（如刚创建的 #63-#71）
2. **指定范围**：用户输入 Issue 编号（如 `63,64,65` 或 `63-71`）
3. **所有 Open Issues**：同步所有未加入 Project 的 Issues

### Phase 3: 执行同步

对于每个 Issue：

1. **添加到 Project**：
```bash
gh project item-add PROJECT_NUMBER --owner OWNER --url ISSUE_URL
```

2. **设置状态列**（根据优先级）：
```bash
# 获取 Item ID
ITEM_ID=$(gh project item-list PROJECT_NUMBER --owner OWNER --format json | jq -r '.items[] | select(.content.url=="ISSUE_URL") | .id')

# 设置状态（需要 field-id 和 option-id）
gh project item-edit --project-id PROJECT_ID --id ITEM_ID --field-id STATUS_FIELD_ID --single-select-option-id OPTION_ID
```

**优先级映射**：
- `priority:p0` → In Progress
- `priority:p1` → Todo
- `priority:p2` → Todo
- `priority:p3` → Backlog
- 无优先级 → Todo

### Phase 4: 输出结果

```
✅ 已同步 9 个 Issues 到 Project "BasicOFR v1.0 Release"

| Issue | 标题 | 状态列 |
|-------|------|--------|
| #63 | [Epic] ModuleCombinator | Todo |
| #64 | Story 1: ModuleParser | In Progress |
| ... | ... | ... |

Project URL: https://github.com/wscffaa/BasicOFR/projects/1
```

## 脚本

### list_projects.py

列出可用的 GitHub Projects。**默认列出仓库级 Projects**。

```bash
# 格式化输出（用于展示，默认仓库级）
python3 scripts/list_projects.py

# JSON 输出（用于解析）
python3 scripts/list_projects.py --json

# 指定 owner（仓库级）
python3 scripts/list_projects.py --owner wscffaa

# 使用用户级 Projects（向后兼容）
python3 scripts/list_projects.py --user
```

**JSON 输出格式**：
```json
{
  "projects": [
    {"number": 1, "title": "BasicOFR v1.0 Release", "id": "PVT_xxx", "url": "https://github.com/wscffaa/BasicOFR/projects/1"}
  ],
  "options": {
    "new_project_index": 2,
    "skip_index": 3
  }
}
```

### create_project.py

创建新 GitHub Project 并初始化 5 个状态列。**默认创建仓库级 Project**。

```bash
# 使用指定名称创建（默认仓库级）
python3 scripts/create_project.py --title "Sprint 1"

# 使用时间戳默认名称
python3 scripts/create_project.py --default

# JSON 输出（用于解析）
python3 scripts/create_project.py --title "Sprint 1" --json

# 指定 owner（仓库级）
python3 scripts/create_project.py --title "Sprint 1" --owner wscffaa

# 创建用户级 Project（向后兼容）
python3 scripts/create_project.py --title "Sprint 1" --user
```

**状态列配置**：
- Backlog (灰色)
- Todo (蓝色)
- In Progress (黄色)
- Review (紫色)
- Done (绿色)

**JSON 输出格式**：
```json
{
  "number": 2,
  "id": "PVT_xxx",
  "title": "Sprint 1",
  "url": "https://github.com/wscffaa/BasicOFR/projects/2"
}
```

## 交互示例

```
User: /gh-project-sync

Claude: 正在获取 GitHub Projects 列表...

请选择目标 GitHub Project：
1. BasicOFR v1.0 Release (Project #1)
2. [新建项目]
3. [跳过]

User: 选择 1

Claude: 请选择要同步的 Issues 范围：
1. 本次创建的 Issues (#63-#71)
2. 指定范围
3. 所有未加入 Project 的 Open Issues

User: 选择 1

Claude: 正在同步 9 个 Issues 到 "BasicOFR v1.0 Release"...

✅ 同步完成！
- 已添加: 9 个 Issues
- In Progress: 1 个 (P0)
- Todo: 6 个 (P1/P2)
- Backlog: 2 个 (P3)

Project URL: https://github.com/wscffaa/BasicOFR/projects/1
```

## 技术约束

- 需要 gh CLI 2.0+ 并已认证
- 需要 `project` scope 权限
- GitHub Projects V2 API

## 与 gh-create-issue 集成

当 `gh-create-issue` 创建 Epic 及 Sub-issues 后，会提示用户运行 `/gh-project-sync`。

**自动触发场景**:
- gh-create-issue 创建 Epic 后的输出末尾会显示同步提示
- 用户确认后，可直接调用 sync_project.py

**示例调用**:
```bash
# 同步刚创建的 Issues 到指定项目
python3 .claude/skills/gh-project-sync/scripts/sync_project.py \
  --project 1 \
  --issues "63-71"

# 同步 Epic 及其所有 Sub-issues
python3 .claude/skills/gh-project-sync/scripts/sync_project.py \
  --project 1 \
  --epic 72
```

## 参考

- PRD: `docs/gh-project-sync-prd.md`
- Epic: #72
