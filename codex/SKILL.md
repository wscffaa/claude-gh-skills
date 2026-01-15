---
name: codex
description: Execute Codex CLI for code analysis, refactoring, and automated code changes. Use when you need to delegate complex code tasks to Codex AI with file references (@syntax) and structured output.
---

# Codex CLI Integration

## Overview

Execute Codex CLI commands with support for multiple models, **image analysis**, and flexible prompt input. Integrates OpenAI's Codex models into Claude Code workflows.

## When to Use

- Complex code analysis requiring deep understanding
- Large-scale refactoring across multiple files
- Automated code generation with safety controls
- **Image analysis** using `-i` parameter (supports PNG, JPG, etc.)
- Alternative perspective on code problems

## Usage

**Recommended**: Run via Python script with fixed timeout 7200000ms:
```bash
python3 ~/.claude/skills/codex/scripts/codex.py "<prompt>" [working_dir]
```

**Direct CLI** (for simple tasks):
```bash
# Basic query
codex exec "explain @src/main.ts"

# With image analysis
echo "分析这张图片的内容" | codex exec -i /path/to/image.png

# Multiple images
echo "对比这两张图片的差异" | codex exec -i before.png -i after.png
```

## Environment Variables

- **CODEX_MODEL**: Configure model (default: `gpt-5.1-codex-max`)
  - Example: `export CODEX_MODEL=gpt-5.2`
- **CODEX_MODEL_REASONING_EFFORT**: Configure reasoning effort
  - Example: `export CODEX_MODEL_REASONING_EFFORT=high`

## Key Features

### 1. Image Analysis (`-i` parameter)

Codex 原生支持图像分析，通过 `-i` 参数传入图片：

```bash
# 单张图片分析
echo "请分析这张图片的内容，描述图像类型、主要内容和质量" | codex exec -i /path/to/image.png

# 多张图片对比
echo "对比这两张图片的差异" | codex exec -i before.png -i after.png

# 修复效果评估
echo "评估老电影修复效果：1) 划痕是否去除 2) 细节是否保留" | codex exec -i output.png

# 使用 Python 脚本（推荐）
python3 ~/.claude/skills/codex/scripts/codex.py "分析图片内容" -i /path/to/image.png
```

### 2. File References (`@` syntax)

使用 `@` 语法引用文件，Codex 会读取文件内容：

```bash
# 分析单个文件
codex exec "explain @src/main.ts"

# 分析多个文件
codex exec "review @src/api.py and @src/models.py for security issues"

# 分析整个目录
codex exec "analyze @. and find potential bugs"
```

### 3. JSON Streaming

获取结构化输出（用于 parallel-agent 集成）：

```bash
# JSON 格式输出
codex exec --json "analyze @src/utils.py"

# 流式 JSON
codex e -C /path/to/dir --json --skip-git-repo-check -
```

## Timeout Control

- **Fixed**: 7200000 milliseconds (2 hours)
- **Bash tool**: Always set `timeout: 7200000` for protection

### Parameters

- `prompt` (required): Task prompt or question
- `working_dir` (optional): Working directory (default: current directory)
- `-i <path>` (optional): Image file(s) for visual analysis

### Return Format

Plain text output from Codex:

```text
Model response text here...
```

Error format (stderr):

```text
ERROR: Error message
```

### Invocation Pattern

When calling via Bash tool:

```yaml
Bash tool parameters:
- command: python3 ~/.claude/skills/codex/scripts/codex.py "<prompt>" [working_dir]
- timeout: 7200000
- description: <brief description of the task>
```

### Examples

**Basic code analysis:**

```bash
python3 ~/.claude/skills/codex/scripts/codex.py "explain @src/main.ts"
# timeout: 7200000
```

**Image analysis:**

```bash
# 分析图像内容和质量
python3 ~/.claude/skills/codex/scripts/codex.py "分析这张图片的内容和损伤情况" -i /path/to/image.png

# 对比两张图像
python3 ~/.claude/skills/codex/scripts/codex.py "对比修复效果" -i input.png -i output.png

# 直接使用 codex CLI
echo "评估修复质量" | codex exec -i output.png
```

**Code refactoring:**

```bash
python3 ~/.claude/skills/codex/scripts/codex.py "refactor @src/utils for performance:
- Extract duplicate code into helpers
- Use memoization for expensive calculations
- Add inline comments for non-obvious logic" "/path/to/project"
# timeout: 7200000
```

**Security review:**

```bash
python3 ~/.claude/skills/codex/scripts/codex.py "analyze @. and find security issues:
1. Check for SQL injection vulnerabilities
2. Identify XSS risks in templates
3. Review authentication/authorization logic
4. Flag hardcoded credentials or secrets" "/path/to/project"
# timeout: 7200000
```

## Comparison with Other Skills

| 特性 | `codex` skill | `gemini` skill | `parallel-agent` |
|------|---------------|----------------|------------------|
| **用途** | 单任务代码/图像分析 | 单任务代码/图像分析 | 多任务 DAG 调度 |
| **图像分析** | ✅ `-i` 参数 | ✅ `@filepath` 语法 | ✅ `images:` 字段 |
| **文件引用** | ✅ `@` 原生语法 | ✅ `@filepath` 或 `$(cat)` | ✅ 通过后端 |
| **代码分析** | ✅ 原生支持 | ✅ 原生支持 | ✅ 通过后端 |
| **JSON 输出** | ✅ `--json` | ✅ `-o stream-json` | ✅ 内置 |
| **代码执行** | ✅ 沙箱执行 | ✅ `-s` 沙箱 | 依赖后端 |
| **响应速度** | ⭐⭐ 中等 | ⭐⭐⭐ 最快 | 依赖后端 |

## Multi-Task Workflows

对于多任务并行执行，请使用 `parallel-agent` skill：

```bash
# parallel-agent 支持 DAG 依赖调度
python3 ~/.claude/skills/parallel-agent/scripts/skill.py --backend codex <<'EOF'
---TASK---
id: analyze
workdir: /path/to/project
---CONTENT---
analyze @spec.md and summarize requirements
---TASK---
id: implement
dependencies: analyze
---CONTENT---
implement features based on analysis
EOF
```

## Notes

- Python implementation using standard library (zero dependencies)
- Cross-platform compatible (Windows/macOS/Linux)
- Requires Codex CLI installed and authenticated
- Supports all Codex model variants (configure via `CODEX_MODEL` environment variable)
- For complex multi-task workflows, use `parallel-agent` skill instead
- Image analysis requires Codex with vision capabilities
