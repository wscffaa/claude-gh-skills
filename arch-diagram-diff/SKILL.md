---
name: arch-diagram-diff
description: Baseline 差异驱动的学术架构图生成器。基于已有 Baseline 架构图进行差异分析和增量修改，支持用户确认门禁。
---

# arch-diagram-diff

## 核心理念

**Baseline 差异驱动**：不从头生成，而是基于已有的高质量 Baseline 架构图进行增量修改。

- 复用 Baseline 的布局、风格、主图结构
- 只修改差异部分（创新点 → inset ABC）
- 保持论文图风格一致性

## 流程

```
[阶段0] 输入 + Baseline 加载
         ↓
[阶段1] 架构差异分析 → diff_analysis.md
         ↓ (暂停，等待 --confirm-diff)
[阶段2] 增量 Schema 生成 → visual_schema.md
         ↓
[阶段3] 图像编辑渲染 → diagram.jpg (inpainting 模式)
```

## 运行

```bash
# 新建任务（指定 Baseline）
python3 skill.py --arch_code_path basicofr/archs/freqmamba_arch.py --baseline MambaOFR

# 用户确认差异分析后继续
python3 skill.py --resume <task_id> --confirm-diff

# 迭代修改
python3 skill.py --resume <task_id> --iterate --feedback "修改需求"

# 列出所有任务
python3 skill.py --list
```

## Baseline 图库

Baseline 图存放在 `.claude/skills/arch-diagram/baselines/` 目录：

- `MambaOFR.jpeg` - MambaOFR 架构图
- `RTN.jpeg` - RTN 架构图

## 参考

- 用法与参数：`references/DETAILS.md`
