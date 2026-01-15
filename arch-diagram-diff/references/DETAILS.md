# 细节（arch-diagram-diff）

## 设计理念

**Baseline 差异驱动**：不从头生成架构图，而是基于已有的高质量 Baseline 架构图进行增量修改。

核心优势：
- 复用 Baseline 的布局、风格、主图结构
- 只修改差异部分（创新点 → inset ABC）
- 保持论文图风格一致性
- 用户可以在差异分析后确认/修改再继续

## 文件系统即状态机

默认输出目录：`experiments/visualizations/architecture-diff/<task_id>/`

```
{task_id}/
├── input.json              # 输入参数
├── baseline_info.json      # Baseline 信息
├── code_snapshot.py        # 新项目代码快照
├── baseline_code_snapshot.py # Baseline 代码快照
├── diff_analysis.md        # [阶段1] 差异分析结果 ← 用户确认点
├── workflow_diagram.md     # [阶段1.5] 工作流程图初稿 ★ 新增
├── diff_confirmed.json     # 用户确认标记
├── visual_schema.md        # [阶段2] Visual Schema
├── versions/
│   └── v1/
│       ├── renderer_prompt.md
│       ├── diagram.jpg     # [阶段3] 最终架构图
│       └── response.txt
└── latest_version.txt
```

## 流程详解

### 阶段0: 输入 + Baseline 加载
- 保存新项目架构代码路径
- 加载 Baseline 图像和代码
- 创建代码快照

### 阶段1: 架构差异分析
- 调用 Codex 对比 Baseline 和新项目代码
- 输出结构化差异报告：
  - 共同模块（可直接复用）
  - 修改模块（需更新图）
  - 新增创新点（→ inset）

### 阶段1.5: 工作流程图初稿 ★★★ 新增
- 在渲染学术级架构图前生成 ASCII 工作流程图
- 包含内容：
  - 流水线架构图 (Pipeline Overview)
  - 创新点数据流详图
  - 与 Baseline 对比图
  - 用户确认清单
- **暂停，等待用户确认**

### 用户确认门禁
- 用户查看 `diff_analysis.md` + `workflow_diagram.md`
- 可手动编辑修改分析结果和工作流图
- 运行 `--confirm-diff` 确认后继续

### 阶段2: 增量 Schema 生成
- 基于 Baseline Schema（如果有）进行增量修改
- 只更新差异部分
- 保留主图结构

### 阶段3: 图像编辑渲染
- 将 Baseline 图作为编辑基础图
- 使用 inpainting 模式修改差异区域
- 保持整体风格一致

## Baseline 管理

### Baseline 图库位置
`.claude/skills/arch-diagram/baselines/`

### 已有 Baseline
- `MambaOFR.jpeg` - MambaOFR 架构图
- `RTN.jpeg` - RTN 架构图

### 添加新 Baseline
1. 将架构图放入 `baselines/` 目录，命名为 `{ProjectName}.jpeg`
2. 在 `skill.py` 的 `BASELINE_CODE_MAP` 中添加代码路径映射（可选）

## 命令参考

```bash
# 新建任务
python3 skill.py --arch_code_path basicofr/archs/freqmamba_arch.py --baseline MambaOFR

# 只运行差异分析
python3 skill.py --arch_code_path arch.py --baseline MambaOFR --diff-only

# 确认差异分析后继续
python3 skill.py --resume <task_id> --confirm-diff

# 确认时添加备注
python3 skill.py --resume <task_id> --confirm-diff --confirm-note "已确认创新点 A/B"

# 迭代修改
python3 skill.py --resume <task_id> --iterate --feedback "将 inset (a) 的布局改为垂直"

# 强制重新执行
python3 skill.py --resume <task_id> --force

# 列出可用 Baseline
python3 skill.py --list-baselines

# 列出所有任务
python3 skill.py --list
```

## 调试技巧

### 重跑某阶段
删除对应阶段的产物文件，然后 `--resume`：
- 重跑差异分析：删除 `diff_analysis.md`
- 重跑 Schema：删除 `visual_schema.md`
- 重跑渲染：删除 `versions/vN/diagram.jpg`

### 手动编辑
- 差异分析不满意：直接编辑 `diff_analysis.md`，然后 `--confirm-diff`
- Schema 需要调整：直接编辑 `visual_schema.md`，删除 `versions/` 后重新渲染

## 依赖

- `codex` CLI：用于阶段1/2
- Python `openai` 包：用于阶段3 图像渲染
- 本地 Gemini Proxy：监听 `127.0.0.1:8888`

## 与 arch-diagram 的区别

| 特性 | arch-diagram | arch-diagram-diff |
|------|-------------|-------------------|
| 生成方式 | 从头生成 | 基于 Baseline 增量修改 |
| 用户确认 | v1 渲染后确认 | 差异分析后确认 |
| 参考图 | 可选 | 必须（Baseline） |
| 风格一致性 | 依赖模板 | 继承 Baseline |
| 适用场景 | 全新架构 | 变体架构 |
