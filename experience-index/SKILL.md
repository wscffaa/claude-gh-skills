---
name: experience-index
description: 自动检索历史经验 + 经验沉淀，实现 Compound Engineering 闭环。触发词：经验检索、经验沉淀、历史错误、预警、规则匹配。
allowed-tools: Read, Glob, Grep, Bash, Write
---

# Experience Index Skill

> **版本**: 2.0
> **来源**: AgentProjectKit experience-index + /optimize-flow 合并

## 核心功能

**双模式设计**：

| 模式 | 功能 | 触发时机 |
|------|------|----------|
| **检索模式** | 从规则文件检索匹配的历史经验 | Pre-Phase、Phase 4/5 前 |
| **沉淀模式** | 从项目产物提取新经验并更新规则 | Post-Phase 7 后（自动） |

形成闭环：**检索 → 开发 → 沉淀 → 检索...**

---

## 检索模式

在关键 Phase 开始前自动检索历史经验，返回：
1. **上下文文档** (context): 相关的历史文档和代码
2. **风险预警** (risk): 匹配的历史错误和预防措施
3. **服务建议** (service): 推荐的 Baseline 和依赖
4. **代码模式** (pattern): 推荐的实现模式和模板

### 使用方法

```bash
# 基本检索
python3 .claude/skills/experience-index/scripts/experience_index.py \
  --scene "wavelet + mamba 创新集成" \
  --project wavemamba \
  --types wavelet,mamba

# JSON 输出（供程序解析）
python3 .claude/skills/experience-index/scripts/experience_index.py \
  --scene "wavelet 小波变换" \
  --project wavemamba \
  --types wavelet \
  --json
```

### 输入参数

| 参数 | 必需 | 说明 |
|------|------|------|
| `--scene` | ✅ | 场景描述（自然语言） |
| `--project` | ✅ | 项目 slug |
| `--types` | ❌ | 创新类型，逗号分隔 |
| `--json` | ❌ | 输出 JSON 格式 |

### 输出格式

```yaml
context:
  files:
    - ".claude/config/ofr-error-knowledge.md#E001"
    - "specs/410-proj-wavemamba/debug/error_report.md"
risk:
  alerts:
    - level: high
      error_id: E001
      message: "AMP + pytorch_wavelets 不兼容，必须禁用 autocast"
    - level: medium
      error_id: E005
      message: "Mamba 需要 causal_conv1d，检查 conda 环境"
service:
  suggestions:
    - baseline: "MambaOFR"
      reason: "Mamba/SSM 创新推荐 MambaOFR 基线"
pattern:
  files:
    - ".claude/agents/ofr-sisyphus.md#代码骨架模板"
    - "basicofr/archs/ideas/wavemamba/wavemamba_arch.py"
```

---

## 沉淀模式

Phase 7 完成后自动提取经验并更新规则文件。

### 使用方法

```bash
# 自动扫描项目产物
python3 .claude/skills/experience-index/scripts/experience_index.py \
  --harvest --project wavemamba

# 手动记录错误
python3 .claude/skills/experience-index/scripts/experience_index.py \
  --harvest --project wavemamba \
  --error "AMP 与 pytorch_wavelets 不兼容导致类型错误"

# 手动记录模式
python3 .claude/skills/experience-index/scripts/experience_index.py \
  --harvest --project wavemamba \
  --pattern "使用 autocast(enabled=False) 包裹小波操作"
```

### 输入参数

| 参数 | 必需 | 说明 |
|------|------|------|
| `--harvest` | ✅ | 启用沉淀模式 |
| `--project` | ✅ | 项目 slug |
| `--error` | ❌ | 手动记录错误描述 |
| `--pattern` | ❌ | 手动记录模式描述 |
| `--json` | ❌ | 输出 JSON 格式 |

### 沉淀逻辑

```
Phase 7 完成
    │
    ▼
扫描项目产物
├─→ specs/{project}/debug/error_report.md
└─→ specs/{project}/backprop/backprop_log.md
    │
    ▼
提取经验
├─→ 无新经验 → 无操作（静默）
└─→ 有新经验 → 更新规则文件
    │
    ▼
追加到规则文件
├─→ risk-rules.md（新错误）
└─→ pattern-rules.md（新模式）
```

---

## 规则文件

规则存储在 `.claude/rules/experience/` 目录下（符合 Claude Code 官方规范）：

| 文件 | 作用 |
|------|------|
| `context-rules.md` | 场景 → 加载文档映射 |
| `risk-rules.md` | 场景 → 风险预警映射 |
| `service-rules.md` | 场景 → Baseline/服务建议 |
| `pattern-rules.md` | 场景 → 代码模式映射 |

> **为什么放在 `.claude/rules/`？**
> Claude Code 新版本支持模块化规则，按目录/子项目生效。
> `experience/` 子目录专门存放经验沉淀规则，与其他项目规则统一管理。

### 规则格式

```markdown
## 规则 N: 规则名称
- 触发条件: 关键词 OR 关键词
- 风险等级: high | medium | low
- 提示信息: 预警信息
- 加载文档: 文档路径
- 建议服务: 服务1, 服务2
- 模式文件: 模式文件路径
```

---

## 与 OFR 工作流集成

### Pre-Phase: 经验检索

```bash
python3 .claude/skills/experience-index/scripts/experience_index.py \
  --scene "{paper_title} {innovation_keywords}" \
  --project {project_slug} \
  --types {innovation_types}
```

### Post-Phase 7: 经验沉淀（自动）

```bash
python3 .claude/skills/experience-index/scripts/experience_index.py \
  --harvest --project {project_slug}
```

---

## 边际成本下降

| 项目序号 | 耗时 | 原因 |
|----------|------|------|
| 第 1 个 | 45 min | 无经验积累 |
| 第 2 个 | 15 min | 自动预警同类错误 |
| 第 3 个 | 5 min | 自动推荐代码模式 |

**Compound Engineering**: 每个项目的经验都沉淀到规则文件，使下一个项目更容易。

---

## lean-spec 集成

沉淀新错误时会自动同步到 lean-spec specs：

```
.claude/config/ofr-error-knowledge.md  →  specs/5xx-std-error-*/README.md
```

**搜索错误**:
```bash
lean-spec search "wavelet" --tags error
lean-spec view 501  # 查看 E001
```

**项目关联错误** (手动):
```yaml
# specs/4xx-proj-{project}/README.md
depends_on:
  - 501-std-error-E001
```

---

## 维护指南

### 添加新规则

1. 确定规则类型（context/risk/service/pattern）
2. 编辑对应的 `rules/*.md` 文件
3. 按格式添加新规则
4. 测试检索效果

### 规则优先级

- `high` 级别预警会在输出中优先展示
- 多个规则匹配时全部返回，由调用方决定处理方式

### 验证规则

```bash
# 测试检索
python3 .claude/skills/experience-index/scripts/experience_index.py \
  --scene "wavelet mamba" --project test --types wavelet,mamba

# 测试沉淀
python3 .claude/skills/experience-index/scripts/experience_index.py \
  --harvest --project test
```
