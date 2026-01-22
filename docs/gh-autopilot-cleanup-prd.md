# Product Requirements Document: gh-autopilot 资源清理机制

**Version**: 1.0
**Date**: 2026-01-21
**Author**: Sarah (Product Owner)
**Quality Score**: 92/100

---

## Executive Summary

gh-autopilot 在执行 Issue 批量实现时会创建 git worktree、本地分支和远端分支。当前实现中，这些资源在流程结束后可能残留，导致仓库污染和磁盘占用。

本需求定义完整的资源清理机制，确保流程结束后（无论成功、失败或中断）自动清理所有本次运行创建的资源，同时提供手动清理入口。

---

## Problem Statement

**Current Situation**:
- `batch_executor.py` 为每个 Issue 创建独立 worktree 和分支
- 清理失败时仅打印 Warning，不阻塞也不强制清理
- PR 合并后远端分支删除，但本地 worktree 和分支可能残留
- 流程中断时资源完全残留
- 无法区分本次运行创建的分支和其他流程创建的分支

**Proposed Solution**:
- 通过状态文件追踪本次运行创建的所有资源
- 在流程结束时（finally 块）统一清理
- 提供 `--cleanup` 子命令支持手动清理
- 清理失败时继续处理其他资源，最后汇总报告

**Business Impact**:
- 避免仓库分支污染（当前残留 20+ 分支）
- 减少磁盘占用（每个 worktree 约 100MB+）
- 提升开发体验和仓库整洁度

---

## Success Metrics

**Primary KPIs:**
- **残留率**: 流程结束后残留资源 = 0（目标 100% 清理率）
- **清理成功率**: 单次运行清理成功率 ≥ 95%
- **用户反馈**: 无需手动清理分支的投诉

**Validation**: 运行 10 次 gh-autopilot 后检查残留资源数量

---

## User Personas

### Primary: CLI 开发者
- **Role**: 使用 gh-autopilot 批量实现 Issue 的开发者
- **Goals**: 自动化开发流程，保持仓库整洁
- **Pain Points**: 每次运行后需要手动清理残留分支和 worktree
- **Technical Level**: Advanced

---

## User Stories & Acceptance Criteria

### Story 1: 自动清理（核心）

**As a** CLI 开发者
**I want to** gh-autopilot 流程结束后自动清理所有本次创建的资源
**So that** 仓库保持整洁，无需手动清理

**Acceptance Criteria:**
- [ ] 流程正常结束时，所有 worktree 被删除
- [ ] 流程正常结束时，所有本地分支被删除
- [ ] 流程正常结束时，所有远端分支被删除
- [ ] 流程失败时，同样执行清理
- [ ] 流程中断（Ctrl+C）时，同样执行清理
- [ ] 清理顺序：worktree → 本地分支 → 远端分支

### Story 2: 精准追踪

**As a** CLI 开发者
**I want to** 仅清理本次运行创建的分支
**So that** 不影响其他流程创建的分支

**Acceptance Criteria:**
- [ ] 在状态文件中记录本次创建的所有 issue 编号
- [ ] 清理时仅删除状态文件中记录的分支
- [ ] 不删除 main/master 等保护分支
- [ ] 不删除其他 gh-autopilot 运行创建的分支

### Story 3: 手动清理

**As a** CLI 开发者
**I want to** 使用 `--cleanup` 命令手动清理残留资源
**So that** 可以在任何时候清理历史残留

**Acceptance Criteria:**
- [ ] `batch_executor.py --cleanup` 清理所有已合并的 issue-* 分支
- [ ] 支持 `--cleanup --force` 清理所有 issue-* 分支（无论是否合并）
- [ ] 输出清理报告：删除了哪些资源

### Story 4: 容错清理

**As a** CLI 开发者
**I want to** 单个资源清理失败时继续清理其他资源
**So that** 不会因为一个失败阻塞整个清理

**Acceptance Criteria:**
- [ ] 单个 worktree 删除失败时，继续删除其他 worktree
- [ ] 单个分支删除失败时，继续删除其他分支
- [ ] 最后输出失败汇总报告
- [ ] 失败的资源尝试 `--force` 删除

---

## Functional Requirements

### Core Features

**Feature 1: 资源追踪**
- 在 `ExecState` 中新增 `created_issues: set[int]` 字段
- 每个 issue 开始处理时，添加到 `created_issues`
- 在状态文件 `scheduler_state.json` 中持久化

**Feature 2: 自动清理（finally 块）**

```python
# batch_executor.py main() finally 块
finally:
    _cleanup_all_resources(state, repo_dir, worktree_script)
```

清理流程：
1. 遍历 `state.created_issues`
2. 对每个 issue：
   - 删除 worktree（失败则 --force）
   - 删除本地分支 `issue-{number}`
   - 删除远端分支 `origin/issue-{number}`
3. 执行 `git worktree prune`
4. 输出清理报告

**Feature 3: 手动清理命令**

```bash
# 清理已合并的 issue-* 分支
python3 batch_executor.py --cleanup

# 强制清理所有 issue-* 分支
python3 batch_executor.py --cleanup --force

# 仅清理指定 issue
python3 batch_executor.py --cleanup --issues 123,124,125
```

**Feature 4: 清理报告**

```
🧹 清理报告:
- Worktrees 删除: 4
- 本地分支删除: 4
- 远端分支删除: 4
- 失败项: 0

✅ 清理完成
```

### Out of Scope
- 不清理 `paper/*` 分支（ofr-pipeline 管理）
- 不清理 `feat/*` 分支（手动创建）
- 不清理 `pr-*` 本地分支（由其他流程创建）

---

## Technical Constraints

### Performance
- 清理操作应在 30 秒内完成（10 个 issue）
- 远端分支删除可并行执行

### Security
- 不删除 main/master/develop 等保护分支
- 不删除未在状态文件中记录的分支

### Integration
- **worktree.py**: 复用现有 create/remove 逻辑
- **状态文件**: 扩展 `scheduler_state.json` 格式

### Technology Stack
- Python 3.8+
- subprocess 调用 git 命令
- 无新增依赖

---

## MVP Scope & Phasing

### Phase 1: MVP (Required for Initial Launch)
- [x] 资源追踪（ExecState.created_issues）
- [x] 自动清理（finally 块）
- [x] 标准清理顺序
- [x] 清理报告输出

### Phase 2: Enhancements (Post-Launch)
- [ ] `--cleanup` 子命令
- [ ] `--cleanup --force` 选项
- [ ] `--cleanup --issues` 指定清理

### Future Considerations
- 定时清理任务
- 清理前确认提示
- 清理日志持久化

---

## Risk Assessment

| Risk | Probability | Impact | Mitigation Strategy |
|------|------------|--------|---------------------|
| 误删其他分支 | Low | High | 仅删除状态文件中记录的分支 |
| worktree 被占用 | Medium | Low | 使用 --force 删除 |
| 远端删除失败 | Medium | Low | 记录失败，不阻塞流程 |

---

## Dependencies & Blockers

**Dependencies:**
- worktree.py: 现有脚本
- git CLI: 系统依赖

**Known Blockers:**
- 无

---

## Appendix

### 修改文件清单

| 文件 | 修改内容 |
|------|----------|
| `batch_executor.py` | 新增清理逻辑 |
| `worktree.py` | 可选：新增 prune 子命令 |

### 状态文件格式扩展

```json
{
  "created_issues": [123, 124, 125],
  "cleanup_status": {
    "123": {"worktree": true, "local_branch": true, "remote_branch": true},
    "124": {"worktree": true, "local_branch": true, "remote_branch": false}
  }
}
```

---

*This PRD was created through interactive requirements gathering with quality scoring to ensure comprehensive coverage of business, functional, UX, and technical dimensions.*
