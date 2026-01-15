---
name: parallel-agent
description: 并行任务执行技能，支持 DAG 依赖调度。纯 Python 实现，支持 Codex/Claude/Gemini 三后端。
---
# Parallel Agent 并行任务执行

## 概述

**parallel-agent 是 `.claude/` 目录下的核心执行引擎**，通过 DAG（有向无环图）依赖调度实现多任务并行执行。

**架构定位**：

```
┌─────────────────────────────────────────────────────────────┐
│                     Commands 层                              │
│  /text-analyze  /vision-analyze  /phase3-code  ...          │
└────────────────────────┬────────────────────────────────────┘
                         │ 调用
                         ▼
┌─────────────────────────────────────────────────────────────┐
│              parallel-agent (核心执行引擎)                   │
│  - DAG 依赖调度                                              │
│  - 多后端支持 (Codex/Claude/Gemini)                          │
│  - 图像分析 (images 字段)                                    │
│  - 会话恢复 (session_id)                                     │
└────────────────────────┬────────────────────────────────────┘
                         │ 调用
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    CLI 后端层                                │
│       codex CLI        claude CLI        gemini CLI         │
└─────────────────────────────────────────────────────────────┘
```

**核心特性**：

- 支持三后端：Codex、Claude、Gemini
- 拓扑排序自动识别并行层
- 依赖失败时自动跳过下游任务
- 统一的 JSON 流解析
- **图像分析**：`images` 字段支持三后端视觉分析
- **模型选择**：`model` 字段支持任务级模型切换

**适用场景**：

- 多架构代码生成（消融实验矩阵）
- 分析-实现-测试工作流
- 独立任务并行加速
- **三模型交叉验证**（text-analyze / vision-analyze）

## 安装

无需安装，纯 Python 实现。确保以下 CLI 工具可用：

| 后端   | 命令       | 安装方式                                 |
| ------ | ---------- | ---------------------------------------- |
| codex  | `codex`  | `npm install -g @anthropic-ai/codex`   |
| claude | `claude` | npm install -g @anthropic-ai/claude-code |
| gemini | `gemini` | npm install -g @google/gemini-cli        |

## 使用方式

### 基本调用

```bash
python3 .claude/skills/parallel-agent/scripts/skill.py --backend <后端> <<'EOF'
---TASK---
id: task1
backend: claude
workdir: /path/to/project
---CONTENT---
任务内容
EOF
```

### 进度报告

使用 `--progress` 参数启用实时进度报告：

```bash
python3 .claude/skills/parallel-agent/scripts/skill.py --progress <<'EOF'
---TASK---
id: code_gen
backend: codex
workdir: /path/to/project
---CONTENT---
实现 WaveletBlock
EOF
```

**工作原理**：
1. 启用后，会在任务 prompt 末尾注入进度报告指令
2. LLM 在每完成一个关键步骤后输出 `[PROGRESS] <描述>`
3. parallel-agent 实时捕获并输出到 stderr

**输出示例**：
```
INFO: [Task code_gen] start backend=codex
INFO: [PROGRESS] code_gen | 读取 models/__init__.py
INFO: [PROGRESS] code_gen | 创建 wavelet_block.py (87行)
INFO: [PROGRESS] code_gen | 运行测试，全部通过
INFO: [Task code_gen] complete exit=0
```

**适用场景**：
- 长时间运行的任务（>5分钟）
- 需要监控任务进度时
- 调试任务执行流程

### 参数说明

| 参数          | 说明                         | 默认值       |
| ------------- | ---------------------------- | ------------ |
| `--backend` | 默认后端（任务未指定时使用） | `codex`    |
| `--timeout` | 单任务超时（秒）             | 7200 (2小时) |
| `--progress` | 启用进度报告（LLM 输出 [PROGRESS] 标记） | 关闭 |

### 环境变量

| 变量                  | 说明       | 默认值    |
| --------------------- | ---------- | --------- |
| `CODEAGENT_BACKEND` | 默认后端   | `codex` |
| `CODEX_TIMEOUT`     | 超时（秒） | 7200      |

## 任务格式

```
---TASK---
id: <任务ID>
backend: <后端>
model: <模型名称>
reasoning_effort: <推理努力级别>
workdir: <工作目录>
dependencies: <依赖1>, <依赖2>
session_id: <会话ID，用于恢复>
---CONTENT---
<任务内容>
```

### 字段说明

| 字段             | 必填 | 说明                                        |
| ---------------- | ---- | ------------------------------------------- |
| `id`           | ✓   | 唯一任务标识，推荐 `<功能>_<时间戳>` 格式 |
| `backend`      |      | 后端类型：`codex`/`claude`/`gemini`   |
| `model`        |      | 模型名称，如 `gpt-5.2` / `claude-opus-4-5` / `gemini-3-pro-preview` |
| `reasoning_effort` |  | Codex 推理努力级别：`minimal`/`low`/`medium`/`high` |
| `workdir`      |      | 工作目录，推荐绝对路径                      |
| `dependencies` |      | 逗号分隔的依赖任务 ID                       |
| `images`       |      | 逗号分隔的图片路径，用于视觉分析            |
| `session_id`   |      | 恢复会话时使用，触发 resume 模式            |

### 图像分析支持

所有后端均支持图像分析，通过 `images` 字段指定图片路径：

**后端处理方式**：
| 后端   | 图像传递方式                              |
| ------ | ---------------------------------------- |
| codex  | `-i <path>` 参数（原生支持）             |
| gemini | `@filepath` 语法嵌入到 prompt 中         |
| claude | 指示使用 Read 工具读取图片               |

**使用示例**：
```bash
python3 .claude/skills/parallel-agent/scripts/skill.py <<'EOF'
---TASK---
id: image_analysis
backend: codex
images: /path/to/output.png, /path/to/gt.png
workdir: /workspace
---CONTENT---
对比这两张图片，分析修复效果
EOF
```

**三模型视觉交叉验证**：
```bash
python3 .claude/skills/parallel-agent/scripts/skill.py <<'EOF'
---TASK---
id: codex_vision
backend: codex
images: /path/to/image.png
workdir: /workspace
---CONTENT---
分析图片质量，评估是否有伪影

---TASK---
id: gemini_vision
backend: gemini
images: /path/to/image.png
workdir: /workspace
---CONTENT---
分析图片质量，评估是否有伪影

---TASK---
id: claude_vision
backend: claude
images: /path/to/image.png
workdir: /workspace
---CONTENT---
分析图片质量，评估是否有伪影

---TASK---
id: consensus
backend: claude
dependencies: codex_vision, gemini_vision, claude_vision
workdir: /workspace
---CONTENT---
整合三个模型的分析结果，判断 2/3 共识
EOF
```

## 后端配置

### 三后端命令映射

| 后端   | 生成的命令                                                                             |
| ------ | ------------------------------------------------------------------------------------- |
| codex  | `codex e -C <workdir> [-m <model>] --json --skip-git-repo-check -`                   |
| claude | `claude -p --verbose --setting-sources '' [--model <model>] --output-format stream-json -` |
| gemini | `gemini -o stream-json -y [-m <model>] -p -`                                         |

**注**：三个后端均支持可选的模型参数（codex/gemini 用 `-m`，claude 用 `--model`）。

### 模型切换（三后端支持）

所有后端都支持通过 `model` 字段为不同任务指定不同模型。Codex 还支持 `reasoning_effort` 字段：

**Anything2OFR 三后端配置（model-config.yml）**：
- **Codex 规划**: `model: gpt-5.2` + `reasoning_effort: high`
- **Codex 代码**: `model: gpt-5.1-codex-max` + `reasoning_effort: medium`
- **Gemini**: `gemini-3-pro-preview` (审核/长上下文)
- **Claude**: `claude-opus-4-5-20251101` (审阅/配置)

**使用示例（三后端混合）**：
```bash
python3 .claude/skills/parallel-agent/scripts/skill.py <<'EOF'
---TASK---
id: code_analysis
backend: codex
model: gpt-5.1-codex-max
reasoning_effort: medium
workdir: /workspace/Code/BasicOFR
---CONTENT---
深度代码分析

---TASK---
id: review
backend: gemini
model: gemini-3-pro-preview
workdir: /workspace/Code/BasicOFR
dependencies: code_analysis
---CONTENT---
审核代码分析结果

---TASK---
id: config_gen
backend: claude
model: claude-opus-4-5-20251101
workdir: /workspace/Code/BasicOFR
dependencies: review
---CONTENT---
生成配置文件
EOF
```

**向后兼容**：
- `model` 字段为空时，使用各后端 CLI 的默认模型配置
- `reasoning_effort` 字段为空时，使用 Codex 配置文件中的默认值
- 不影响现有不带 `model` 或 `reasoning_effort` 字段的任务配置

**reasoning_effort 工作原理**：
- 通过环境变量 `CODEX_MODEL_REASONING_EFFORT` 传递给 Codex CLI
- 仅对 Codex 后端有效，其他后端忽略此字段
- 可选值：`minimal`（最快）、`low`、`medium`（平衡）、`high`（最深度推理）

### Resume 模式命令

| 后端   | Resume 命令                                                                                        |
| ------ | ------------------------------------------------------------------------------------------------- |
| codex  | `codex e [-m <model>] --json --skip-git-repo-check resume <session_id> -`                       |
| claude | `claude -p --verbose --setting-sources '' [--model <model>] --output-format stream-json -r <session_id> -` |
| gemini | `gemini -o stream-json -y [-m <model>] -r <session_id> -p -`                                    |

### JSON 解析格式

每个后端的 stream-json 输出格式不同，解析器自动适配：

**Codex**:

```json
{"type": "item.completed", "thread_id": "xxx", "item": {"type": "agent_message", "text": "..."}}
```

**Claude**:

```json
{"type": "result", "session_id": "xxx", "result": "..."}
```

**Gemini**:

```json
{"session_id": "xxx", "content": "..."}
```

## 执行流程

```
任务解析 → 拓扑排序 → 分层执行 → 结果汇总
    │           │           │           │
    └→ parse_tasks()        │           │
                └→ topological_layers() │
                            └→ asyncio.gather() 并行
                                        └→ format_summary()
```

### 依赖处理

1. 无依赖的任务在第一层并行执行
2. 有依赖的任务等待依赖完成后执行
3. 依赖失败时，下游任务标记为 `SKIPPED`

## 使用示例

### 示例 1：单任务执行

```bash
python3 .claude/skills/parallel-agent/scripts/skill.py --backend claude <<'EOF'
---TASK---
id: analyze
backend: claude
workdir: /Users/caifeifan/SCI/BasicOFR
---CONTENT---
分析 @README.md 的项目结构
EOF
```

### 示例 2：多任务并行

```bash
python3 .claude/skills/parallel-agent/scripts/skill.py <<'EOF'
---TASK---
id: task-a
backend: claude
workdir: /path/to/project
---CONTENT---
任务 A 内容

---TASK---
id: task-b
backend: codex
workdir: /path/to/project
---CONTENT---
任务 B 内容

---TASK---
id: task-c
backend: claude
workdir: /path/to/project
dependencies: task-a, task-b
---CONTENT---
任务 C 依赖 A 和 B
EOF
```

### 示例 3：消融实验矩阵（带模型选择）

```bash
python3 .claude/skills/parallel-agent/scripts/skill.py <<'EOF'
---TASK---
id: arch_base
backend: codex
model: gpt-5.1-codex-max
workdir: /workspace/Code/BasicOFR
---CONTENT---
实现 baseline 架构

---TASK---
id: arch_a
backend: codex
model: gpt-5.1-codex-max
workdir: /workspace/Code/BasicOFR
dependencies: arch_base
---CONTENT---
在 base 基础上添加创新 A

---TASK---
id: config_base
backend: claude
workdir: /workspace/Code/BasicOFR
---CONTENT---
生成 base 训练配置

---TASK---
id: config_a
backend: claude
workdir: /workspace/Code/BasicOFR
---CONTENT---
生成 variant A 训练配置

---TASK---
id: test_all
backend: codex
model: gpt-5.1-codex-max
workdir: /workspace/Code/BasicOFR
dependencies: arch_base, arch_a
---CONTENT---
生成测试脚本覆盖所有架构
EOF
```

### 示例 4：会话恢复

```bash
# 使用上次失败任务的 session_id 恢复
python3 .claude/skills/parallel-agent/scripts/skill.py <<'EOF'
---TASK---
id: retry-task
session_id: 4d0848fd-2088-4b4b-aaac-84ddfd9c5510
workdir: /path/to/project
---CONTENT---
继续上次的任务
EOF
```

### 示例 5：Phase 0.5 三研究者并行（Codex 双模型策略）

**场景**：Anything2OFR 工作流中的关键阶段，需要同时使用规划模型和代码模型。

```bash
python3 .claude/skills/parallel-agent/scripts/skill.py <<'EOF'
---TASK---
id: technical_analysis
backend: codex
model: gpt-5.1-codex-max
reasoning_effort: medium
workdir: /workspace/Code/BasicOFR
---CONTENT---
## 角色：技术研究者
分析创新点的代码机制、算法细节、数学推导
输出: .claude/specs/{project}/analysis/technical/

---TASK---
id: academic_analysis
backend: gemini
workdir: /workspace/Code/BasicOFR
---CONTENT---
## 角色：学术研究者
分析领域背景、现有方法对比、SOTA 定位
输出: .claude/specs/{project}/analysis/academic/

---TASK---
id: application_analysis
backend: claude
workdir: /workspace/Code/BasicOFR
---CONTENT---
## 角色：综合研究者
分析应用场景、退化特性、审稿人视角
输出: .claude/specs/{project}/analysis/application/

---TASK---
id: integration
backend: codex
model: gpt-5.2
reasoning_effort: high
workdir: /workspace/Code/BasicOFR
dependencies: technical_analysis, academic_analysis, application_analysis
---CONTENT---
## 任务：整合三方分析
读取三方分析结果，识别共识、处理分歧
输出: statistics.json + theory_validation_report.md
EOF
```

**关键点**：
- 前 3 个任务并行执行（技术、学术、应用分析）
- 第 4 个任务使用 **规划模型** `gpt-5.2` + `reasoning_effort: high` 进行仲裁整合
- 前 3 个任务中的 codex 任务使用 **代码模型** `gpt-5.1-codex-max` + `reasoning_effort: medium`
- DAG 自动调度：前 3 个完成后才执行整合任务

## 输出格式

```
=== Parallel Execution Summary ===
Total: 3 | Success: 2 | Failed: 1

--- Task: task-a ---
Status: SUCCESS
Session: abc123-def456

任务输出内容...

--- Task: task-b ---
Status: FAILED (exit code 1)
Error: 错误信息

--- Task: task-c ---
Status: SKIPPED
Reason: skipped due to failed dependencies: task-b
```

## 退出码

| 退出码 | 含义                     |
| ------ | ------------------------ |
| 0      | 所有任务成功             |
| 1      | 至少一个任务失败或被跳过 |
| 124    | 任务超时                 |
| 127    | 后端命令未找到           |

## 架构说明

```
.claude/skills/parallel-agent/
├── SKILL.md               # 本文档
└── scripts/
    ├── skill.py           # 主入口
    ├── task_parser.py     # 任务解析器
    ├── dag_scheduler.py   # DAG 拓扑排序
    ├── executor.py        # 异步执行器
    └── json_parsers.py    # 多后端 JSON 解析
```

### 模块职责

| 模块                 | 职责                                         |
| -------------------- | -------------------------------------------- |
| `task_parser.py`   | 解析 `---TASK---` / `---CONTENT---` 格式 |
| `dag_scheduler.py` | 拓扑排序，检测循环依赖                       |
| `executor.py`      | 构建命令、异步执行、超时处理                 |
| `json_parsers.py`  | 解析 Codex/Claude/Gemini 的 stream-json      |

## 测试验证

### 单元测试

```bash
cd .claude/skills/parallel-agent/scripts

# JSON 解析器测试
python3 -c "
from json_parsers import parse_claude_stream
lines = ['{\"session_id\": \"abc\", \"result\": \"OK\"}']
result = parse_claude_stream(lines)
assert result.session_id == 'abc'
assert result.message == 'OK'
print('Claude JSON 解析: PASS')
"

# 任务解析测试
python3 -c "
from task_parser import parse_tasks
data = '''---TASK---
id: t1
---CONTENT---
content'''
tasks = parse_tasks(data)
assert len(tasks) == 1
assert tasks[0].task_id == 't1'
print('任务解析: PASS')
"

# DAG 调度测试
python3 -c "
from task_parser import parse_tasks
from dag_scheduler import topological_layers
data = '''---TASK---
id: a
---CONTENT---
A
---TASK---
id: b
---CONTENT---
B
---TASK---
id: c
dependencies: a, b
---CONTENT---
C'''
tasks = parse_tasks(data)
layers = topological_layers(tasks)
assert len(layers) == 2
assert len(layers[0]) == 2  # a, b 并行
print('DAG 调度: PASS')
"
```

### 端到端测试

**Claude 后端**:

```bash
python3 .claude/skills/parallel-agent/scripts/skill.py --backend claude --timeout 30 <<'EOF'
---TASK---
id: test-claude
backend: claude
workdir: /tmp
---CONTENT---
回复 "CLAUDE_OK"
EOF
```

**Codex 后端**:

```bash
python3 .claude/skills/parallel-agent/scripts/skill.py --backend codex --timeout 60 <<'EOF'
---TASK---
id: test-codex
backend: codex
workdir: /tmp
---CONTENT---
回复 "CODEX_OK"
EOF
```

**Gemini 后端**:

```bash
python3 .claude/skills/parallel-agent/scripts/skill.py --backend gemini --timeout 30 <<'EOF'
---TASK---
id: test-gemini
backend: gemini
workdir: /tmp
---CONTENT---
回复 "GEMINI_OK"
EOF
```

**混合后端并行测试**:

```bash
python3 .claude/skills/parallel-agent/scripts/skill.py --timeout 60 <<'EOF'
---TASK---
id: claude-task
backend: claude
workdir: /tmp
---CONTENT---
回复 "CLAUDE"

---TASK---
id: codex-task
backend: codex
workdir: /tmp
---CONTENT---
回复 "CODEX"

---TASK---
id: final-task
backend: claude
workdir: /tmp
dependencies: claude-task, codex-task
---CONTENT---
回复 "FINAL"
EOF
```

## 最佳实践

### 后端选择

| 任务类型     | 推荐后端 | 原因           |
| ------------ | -------- | -------------- |
| 代码生成     | Codex    | 深度代码推理   |
| 代码审阅     | Claude   | 快速响应       |
| 长上下文分析 | Gemini   | 超长上下文窗口 |
| 配置生成     | Claude   | 模板化任务     |

### 依赖设计

1. **保持链短**：只添加真正需要顺序的依赖
2. **最大化并行**：独立任务不要加假依赖
3. **命名规范**：使用 `<功能>_<时间戳>` 格式

### 错误处理

1. **保存 Session ID**：用于会话恢复
2. **合理超时**：复杂任务设置足够长的超时
3. **依赖隔离**：失败任务不影响无关任务

## 故障排除

### 常见错误

| 错误                                               | 原因            | 解决方案                |
| -------------------------------------------------- | --------------- | ----------------------- |
| `parallel config is empty`                       | stdin 为空      | 检查输入格式            |
| `missing id field`                               | 任务缺少 id     | 添加 `id: xxx`        |
| `cycle detected`                                 | 循环依赖        | 检查 dependencies       |
| `backend command not found`                      | CLI 未安装      | 安装对应后端 CLI        |
| `--output-format=stream-json requires --verbose` | Claude 参数问题 | 已在 executor.py 中修复 |

### 长时间任务监控

对于可能运行较长时间的任务，建议：
1. 使用 `--progress` 参数查看实时进度
2. 进度输出可帮助判断任务是否正常进行
3. 如果长时间无进度输出，可能是任务卡住

### 调试模式

查看详细日志：

```bash
python3 .claude/skills/parallel-agent/scripts/skill.py --backend claude 2>&1 <<'EOF'
...
EOF
```

日志输出到 stderr，包含：

- `INFO: Loaded N tasks in M layers`
- `INFO: [Task xxx] start backend=xxx`
- `INFO: [Task xxx] command: ...`
- `WARN: [Task xxx] error message`
- `INFO: [Task xxx] complete exit=N`

## 与 Commands 集成

parallel-agent 作为核心引擎，被多个 commands 调用：

### /text-analyze

三模型文本交叉验证，生成 DAG 任务调用 parallel-agent：

```yaml
---TASK---
id: gemini_analyze
backend: gemini
---CONTENT---
{prompt_from_template}

---TASK---
id: codex_analyze
backend: codex
---CONTENT---
{prompt_from_template}

---TASK---
id: claude_analyze
backend: claude
---CONTENT---
{prompt_from_template}
```

**Prompt 模板**：`.claude/templates/` 下的预设模板
- `sci-storyline-prompt.md` - Phase 0.5 故事线
- `alignment-prompt.md` - Phase 1 框架对齐
- `planning-prompt.md` - Phase 2 集成规划
- `code-review-prompt.md` - Phase 3 代码审查
- `paper-review-prompt.md` - Phase 5 论文审核
- `text-general-prompt.md` - 通用分析

### /vision-analyze

三模型视觉交叉验证，使用 `images` 字段：

```yaml
---TASK---
id: gemini_vision
backend: gemini
images: {image_path}
---CONTENT---
{prompt}

---TASK---
id: codex_vision
backend: codex
images: {image_path}
---CONTENT---
{prompt}

---TASK---
id: claude_vision
backend: claude
images: {image_path}
---CONTENT---
{prompt}
```

**Prompt 模板**：`.claude/templates/vision-analyze-prompt.md`

### 共识判断

commands 在收到 parallel-agent 结果后执行共识判断：

**文本分析**：
- 从各模型响应提取 `X/10` 评分
- 评分差异 ≤ 2 分 → 达成共识
- 计算平均分和可信度

**视觉分析**：
- 关键词匹配（正向/负向）
- 2/3 模型一致 → 达成共识

## 相关文件

| 类型 | 路径 | 说明 |
|------|------|------|
| **Skill** | `.claude/skills/parallel-agent/` | 本目录，核心引擎 |
| **Commands** | `.claude/commands/text-analyze.md` | 文本分析命令 |
| **Commands** | `.claude/commands/vision-analyze.md` | 视觉分析命令 |
| **Templates** | `.claude/templates/*.md` | Prompt 模板 |
| **Wrapper** | `.claude/skills/codex/` | Codex 单任务 wrapper |
| **Wrapper** | `.claude/skills/gemini/` | Gemini 单任务 wrapper |
