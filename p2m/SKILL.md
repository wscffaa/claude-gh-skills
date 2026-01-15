---
name: p2m
description: P2M (Paper to Markdown) - 将学术论文 PDF 转换为 markdown 格式，自动下载并规范化核心代码。支持 arXiv ID 自动下载 PDF，使用 Codex 智能识别并生成符合项目规范的代码。
---

# P2M (Paper to Markdown) Skill

将学术论文 PDF 转换为 Markdown，并自动生成规范化的核心代码。基于 marker-pdf + Codex。

## 核心特性

- **高质量转换**：基于 marker-pdf，保留文档结构、公式、表格
- **支持 arXiv**：输入 arXiv ID 自动下载 PDF 并转换
- **智能命名**：使用 Codex 生成 CamelCase 风格的文件夹名称
- **自动下载代码**：从论文摘要提取 GitHub URL，使用 Codex 识别核心架构文件
- **规范化代码生成**：生成符合项目规范的代码（移除 mmcv 依赖、添加中文注释）
- **增量处理**：检测已存在的论文，自动跳过转换直接进入代码提取

## 工作流程

```
┌──────────────────────────────────────────────────────────────────┐
│                     Phase 0: 检查已存在                          │
│  检测 Ideas/<Name>/Paper/*.md 是否存在                           │
│  如存在 → 跳过转换，直接进入代码阶段                               │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                     Phase 1: 论文转换                            │
│  下载 PDF (arXiv) → marker 转换 → 保存到 /Paper/                  │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                     Phase 2: 代码下载                            │
│  提取 GitHub URL → 获取仓库结构 → Codex 识别核心文件                │
│  下载到 /Codes/ → 解析依赖 → 递归下载                             │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                     Phase 3: 代码规范化                           │
│  Codex 分析原始代码 → 移除框架依赖 → 添加中文注释                    │
│  生成 <Name>.py + utils.py + __init__.py 到项目根目录              │
└──────────────────────────────────────────────────────────────────┘
```

## 依赖安装

```bash
# 核心依赖
pip install marker-pdf requests tqdm

# 智能命名和代码规范化（需要 codex CLI）
# 如果没有 codex，会自动 fallback 到规则匹配
```

## 使用方式

### 基本用法

```bash
# arXiv ID（自动下载论文 + 生成规范化代码）
/p2m 2501.04486

# 手动指定 GitHub 仓库
/p2m 2501.04486 --github https://github.com/FVL2020/MB-TaylorFormerV2

# 仅下载论文，跳过代码
/p2m 2501.04486 --no-code

# 仅提取代码（论文已存在时）
/p2m 2501.04486 --code-only

# 本地 PDF
/p2m /path/to/paper.pdf

# 覆盖已存在的输出
/p2m 2501.04486 --overwrite
```

### 直接调用脚本

```bash
python3 .claude/skills/p2m/scripts/paper2markdown.py 2501.04486
python3 .claude/skills/p2m/scripts/paper2markdown.py 2501.04486 --github https://github.com/xxx/yyy
python3 .claude/skills/p2m/scripts/paper2markdown.py 2501.04486 --code-only
```

## 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `input` | 位置参数 | 必填 | PDF 文件路径或 arXiv ID |
| `--out-dir` | 可选 | `./Ideas/` | 输出基目录 |
| `--github` | 可选 | 自动提取 | GitHub 仓库地址 |
| `--no-code` | 开关 | False | 跳过代码下载和规范化 |
| `--code-only` | 开关 | False | 仅提取代码（要求论文已存在） |
| `--overwrite` | 开关 | False | 覆盖已存在的输出 |
| `--page-range` | 可选 | 全部 | 页面范围（如 0-9） |

## 输出结构

```
Ideas/
└── DefMamba/                    # 项目目录
    ├── Paper/                   # 论文目录
    │   ├── DefMamba.md          # 论文 Markdown
    │   └── *.jpeg               # 提取的图片
    └── Codes/                   # 规范化代码目录
        ├── DefMamba.py          # ✅ 规范化主架构（有中文注释）
        ├── utils.py             # ✅ 规范化工具函数
        ├── __init__.py          # ✅ 模块导出
        └── _source.json         # 来源元数据
```

### 规范化代码示例

**生成的 DefMamba.py**（规范格式）：
```python
"""
DefMamba 主体网络

核心流程：
1) PatchMerging2D：逐 stage 下采样聚合空间邻域。
2) DSSM：可变形状态空间模块，三向扫描 + 路径重排实现长程依赖建模。
3) VSSBlock/VSSM：堆叠 DSSM 与 MLP，配合 DropPath 做正则。
"""
import torch
import torch.nn as nn
from timm.layers import DropPath, trunc_normal_
from einops import rearrange

class DSSM(nn.Module):
    """
    Deformable State Space Module
    1) 通道扩展 + DWConv 获取局部先验。
    2) 三向选择性扫描建模长程依赖。
    """
    def __init__(self, d_model=96, d_state=16, ...):
        super().__init__()
        # 初始化参数

    def forward(self, x):
        # (B, H, W, C) -> (B, H, W, C)
        return out
```

**生成的 __init__.py**（位于 Codes/ 目录）：
```python
"""
DefMamba - 可变形状态空间模型

核心模块：
- VSSM: Visual State Space Model 主网络
- Backbone_VSSM: 用于下游任务的骨干网络
- DSSM: Deformable State Space Module
"""
from .DefMamba import VSSM, Backbone_VSSM, DSSM
__all__ = ['VSSM', 'Backbone_VSSM', 'DSSM']
```

## 增量处理说明

当再次运行 `/p2m` 时：

1. **检测已存在的论文**：
   - 扫描 `Ideas/*/Paper/*.md`
   - 通过 arXiv ID 或文件名匹配

2. **如果论文已存在**：
   - 跳过 PDF 下载和 marker 转换
   - 直接读取已有的 markdown 内容
   - 进入代码下载和规范化阶段

3. **使用场景**：
   - 首次运行：完整流程（论文 + 代码）
   - 再次运行：仅更新代码（跳过论文转换）
   - `--overwrite`：强制重新生成全部内容

## 代码规范化策略

### Codex 智能规范化

使用 Codex 分析原始代码并生成规范版本：

1. **依赖替换**：
   - `mmcv/mmengine/mmdet` → PyTorch/timm 原生实现
   - `BaseModule` → `nn.Module`
   - `@BACKBONES.register_module()` → 移除装饰器

2. **保留依赖**：
   - torch, torchvision
   - timm (DropPath, trunc_normal_ 等)
   - einops
   - mamba_ssm, selective_scan_cuda

3. **代码质量**：
   - 中文注释说明核心逻辑
   - 张量维度在注释中标明 `# (B, C, H, W)`
   - 每个类有功能说明

### 规则匹配（Fallback）

当 Codex 不可用时，使用关键词评分识别核心文件：
- 优先关键词：arch, network, net, model, former, mamba, attention
- 排除关键词：__init__, base, utils, train, test, loss, data

## 日志输出示例

```
📄 检测到已存在的论文: Ideas/DefMamba/Paper/DefMamba.md
📁 项目目录: DefMamba
⏭️ 跳过论文转换，直接进入代码提取阶段

🔗 检测到 GitHub: https://github.com/xxx/DefMamba
📂 获取仓库结构...
🤖 Codex 分析核心文件...
🤖 Codex 识别到 2 个核心文件
✅ 下载: vmamba.py
✅ 下载: model.py
🔍 解析代码依赖...
  ✅ 依赖: utils.py
🔧 规范化代码...
🤖 Codex 正在规范化代码...
✅ 规范化代码生成完成: Ideas/DefMamba/Codes
✅ 代码保存: Ideas/DefMamba/Codes (3 文件)

✅ 输出文件：Ideas/DefMamba/Paper/DefMamba.md
```

## arXiv ID 格式

支持新式 arXiv ID（可选 `arxiv:` 前缀、可选版本号）：
- `2301.12345` / `2301.1234`
- `2301.12345v2`
- `arxiv:2301.12345`

## 最佳实践

1. **首次使用**：直接运行 `/p2m <arXiv ID>`，等待完整流程
2. **更新代码**：再次运行 `/p2m <arXiv ID>`，自动跳过论文转换
3. **强制刷新**：使用 `--overwrite` 重新生成全部内容
4. **仅提取代码**：使用 `--code-only` 仅在论文存在时提取代码

## 技术实现

1. **Phase 0 - 检查已存在**
   - 扫描 `Ideas/*/Paper/*.md`
   - 通过 arXiv ID 或文件名匹配已存在的项目

2. **Phase 1 - 论文转换**
   - 输入解析：支持本地 PDF 或 arXiv ID
   - arXiv 下载：`requests` 拉取 PDF
   - marker-pdf 转换：`marker_single --output_format markdown`
   - Codex 命名：生成 CamelCase 文件夹名
   - 输出整理：保存到 `Ideas/<PaperName>/Paper/`

3. **Phase 2 - 代码下载**
   - GitHub URL 提取：从论文摘要正则匹配
   - 仓库结构获取：GitHub API `git/trees`
   - 核心文件识别：Codex 分析 + 规则匹配 fallback
   - 文件下载：`raw.githubusercontent.com` 直接下载
   - 依赖解析：递归下载本地导入的文件

4. **Phase 3 - 代码规范化**
   - 原始代码分析：读取 Codes/ 下的 Python 文件
   - Codex 规范化：生成符合项目规范的代码
   - 输出生成：`<Name>.py` + `utils.py` + `__init__.py`
