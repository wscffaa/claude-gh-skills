---
name: arch-diagram
description: 学术架构图生成器 - 文件系统即状态机设计。支持断点续传、极致透明调试。
---

# 学术架构图生成器 (v2.1 - 文件系统即状态机)

## 设计理念

采用**文件系统即状态机**设计模式：

- **断点续传**：程序随时可中断，重启后自动接续，不浪费 Token
- **极致透明**：所有中间产物可查看，prompt 和响应一目了然
- **可复现**：`input.json` + `code_snapshot.py` 完全复现任务

## 目录结构

```
experiments/visualizations/architecture/{task_id}/
├── input.json          # [阶段0] 输入参数存档
├── code_snapshot.py    # [阶段0] 代码快照（便于复现）
├── analysis.md         # [阶段1] 代码分析结果
├── architect_prompt.md # [阶段2] 发给 Architect 的完整 prompt
├── visual_schema.md    # [阶段2] Visual Schema 输出 ← 存档点
├── renderer_prompt.md  # [阶段3] 发给 Renderer 的完整 prompt
├── diagram.jpg         # [阶段3] 最终架构图
└── response.txt        # [阶段3] Renderer 完整响应
```

## 使用方式

### 新建任务

```bash
# 从代码生成架构图
python3 .claude/skills/arch-diagram/scripts/skill.py \
    --arch_code_path basicofr/archs/freqmamba_arch.py

# 带参考图风格引导
python3 .claude/skills/arch-diagram/scripts/skill.py \
    --arch_code_path basicofr/archs/freqmamba_arch.py \
    --reference_images docs/ref.jpg
```

### 断点续传

```bash
# 查看所有任务状态
python3 .claude/skills/arch-diagram/scripts/skill.py --list

# 恢复中断的任务
python3 .claude/skills/arch-diagram/scripts/skill.py \
    --resume freqmamba_arch_20251231_160000

# 强制重新执行
python3 .claude/skills/arch-diagram/scripts/skill.py \
    --resume freqmamba_arch_20251231_160000 --force
```

### 斜杠命令

```bash
/arch-diagram basicofr/archs/freqmamba_arch.py
/arch-diagram --list
/arch-diagram --resume freqmamba_arch_20251231_160000
```

## 命令行参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `--arch_code_path` | string | 架构代码路径（代码分析模式） |
| `--paper_content` | string | 论文内容（论文模式） |
| `--resume` | string | 恢复已有任务（任务 ID） |
| `--list` | flag | 列出所有任务及状态 |
| `--force` | flag | 强制重新执行所有阶段 |
| `--output_path` | string | 输出目录（默认: experiments/visualizations/architecture） |
| `--model_architect` | string | Architect 模型（默认: gpt-5.2） |
| `--model_renderer` | string | Renderer 模型（默认: gemini-3-pro-image-16x9-4k） |
| `--reference_images` | string[] | 参考图像路径（支持多张） |

## 工作流程

```
阶段0: 保存输入
├── 保存 input.json（参数存档）
└── 复制 code_snapshot.py（代码快照）

阶段1: 代码分析
├── 调用 Codex 分析架构代码
└── 输出 analysis.md

阶段2: 生成 Visual Schema
├── 加载 Architect 模板
├── 保存 architect_prompt.md（完整 prompt）
├── 调用 Codex 生成 Schema
└── 输出 visual_schema.md ← 存档点

阶段3: 渲染架构图
├── 加载 Renderer 模板
├── 保存 renderer_prompt.md（完整 prompt）
├── 调用 Gemini Imagen 3 渲染
├── 输出 diagram.jpg
└── 输出 response.txt（完整响应）
```

## 状态显示

运行时显示实时进度：

```
🎯 Task: freqmamba_arch_20251231_160000
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[✓] input.json              (保存输入参数)
[✓] code_snapshot.py        (代码快照)
[✓] analysis.md             (代码分析)
[✓] architect_prompt.md     (Architect Prompt)
[✓] visual_schema.md        (Visual Schema)
[ ] renderer_prompt.md      (Renderer Prompt)
[ ] diagram.jpg             (架构图)
[ ] response.txt            (完整响应)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## 调试技巧

### 1. 检查 Prompt

觉得结果不对？直接查看发送的 prompt：

```bash
# 查看 Architect prompt
cat experiments/visualizations/architecture/{task_id}/architect_prompt.md

# 查看 Renderer prompt
cat experiments/visualizations/architecture/{task_id}/renderer_prompt.md
```

### 2. 编辑中间产物

手动修改 `visual_schema.md` 后，删除 `diagram.jpg` 重新渲染：

```bash
rm experiments/visualizations/architecture/{task_id}/diagram.jpg
python3 skill.py --resume {task_id}
```

### 3. 只重新渲染

如果只想重新渲染（保留 Schema）：

```bash
rm experiments/visualizations/architecture/{task_id}/diagram.jpg
rm experiments/visualizations/architecture/{task_id}/response.txt
rm experiments/visualizations/architecture/{task_id}/renderer_prompt.md
python3 skill.py --resume {task_id}
```

## 模型配置

### Architect 模型

通过 `config.json` 或命令行 `--model_architect` 配置：

| 模型 | 说明 |
|------|------|
| `gpt-5.2` | 默认，推荐 |
| `gpt-5.1-codex-max` | 代码分析能力强 |

### Renderer 模型

| 宽高比 | 模型 | 分辨率 |
|--------|------|--------|
| 16:9 | `gemini-3-pro-image-16x9-4k` | 1216×896 |
| 4:3 | `gemini-3-pro-image-4k` | 1024×768 |
| 1:1 | `gemini-3-pro-image-4k` | 1024×1024 |

## 与 Agent 的关系

本 Skill 可以独立运行，也可以被 Agent 调用：

- **独立运行**：通过命令行或斜杠命令
- **Agent 调用**：`arch-diagram-architect` 和 `arch-diagram-renderer` Agent 可以调用本 Skill 的各阶段

## 依赖

- Codex CLI (`npm install -g @anthropic-ai/codex`)
- OpenAI SDK (`pip3 install openai`)
- Gemini API 代理运行在 `127.0.0.1:8888`

## 版本历史

### v2.1 (2025-12-31)
- 采用"文件系统即状态机"设计
- 支持断点续传
- 保存所有中间产物（prompt、响应）
- 新增 `--list`、`--resume`、`--force` 参数

### v2.0
- 重构为 Agent 模式
- 支持分阶段执行

### v1.0
- 初始版本
- Architect → Renderer 两步流程
