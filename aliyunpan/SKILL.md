---
name: aliyunpan
description: |
  阿里云盘实验文件管理工具。支持上传/下载/同步 BasicOFR 实验产物（experiments/tb_logger/results），自动生成 manifest 追踪记录。
  触发词：aliyunpan、云盘上传、云盘下载、实验同步、上传实验、下载实验、upload-exp、download-exp、sync-exp、gen-manifest。
  与 ofr-exp-train（原 ofr-experiment）工作流集成，在 complete-exp 后可触发上传。
---

# Aliyunpan 实验文件管理

管理 BasicOFR 项目的实验产物与阿里云盘的同步。

## 安装与登录

首次使用前需安装 aliyunpan CLI 并完成登录：

```bash
# macOS
brew install tickstep/tap/aliyunpan

# Linux
wget https://github.com/tickstep/aliyunpan/releases/latest/download/aliyunpan-linux-amd64.zip
unzip aliyunpan-linux-amd64.zip && chmod +x aliyunpan && sudo mv aliyunpan /usr/local/bin/

# 登录（浏览器扫码）
aliyunpan login

# 验证
aliyunpan who
```

检查状态：
```bash
python3 .claude/skills/aliyunpan/scripts/aliyunpan_ops.py check
```

## 命令

### upload-exp - 上传实验产物

将项目的 experiments/tb_logger/results 目录上传到云盘。

```bash
# 基本用法
python3 scripts/aliyunpan_ops.py upload-exp --project <name>

# 指定时间戳
python3 scripts/aliyunpan_ops.py upload-exp -p dswinir -t 20260109_143000

# 预览模式
python3 scripts/aliyunpan_ops.py upload-exp -p dswinir --dry-run

# 额外排除规则
python3 scripts/aliyunpan_ops.py upload-exp -p dswinir -e "\.ckpt$" -e "\.pth$"
```

**默认排除**：`.git`, `__pycache__`, `*.pyc`, `.DS_Store`, `.env`, `credentials*`

### download-exp - 下载云盘文件

```bash
# 下载到 downloads/
python3 scripts/aliyunpan_ops.py download-exp --cloud /BasicOFR/dswinir/20260109/

# 指定保存目录
python3 scripts/aliyunpan_ops.py download-exp -c /BasicOFR/dswinir/ -s /path/to/save

# 覆盖已存在文件
python3 scripts/aliyunpan_ops.py download-exp -c /BasicOFR/dswinir/ --overwrite
```

### sync-exp - 同步实验目录

持续同步本地与云盘目录。

```bash
# 上传模式（本地 → 云盘）
python3 scripts/aliyunpan_ops.py sync-exp --project dswinir --mode upload

# 下载模式（云盘 → 本地）
python3 scripts/aliyunpan_ops.py sync-exp -p dswinir -m download
```

### list-cloud - 列出云盘文件

```bash
# 列出根目录
python3 scripts/aliyunpan_ops.py list-cloud

# 列出项目目录
python3 scripts/aliyunpan_ops.py list-cloud /BasicOFR/dswinir/

# 详细列表
python3 scripts/aliyunpan_ops.py list-cloud /BasicOFR/ --detailed
```

### gen-manifest - 生成上传清单

生成 JSON manifest 用于追踪上传内容。

```bash
# 基本用法
python3 scripts/manifest_gen.py --project dswinir

# 输出到文件
python3 scripts/manifest_gen.py -p dswinir -o specs/xxx-proj-dswinir/analysis/remote_runs/

# 格式化输出
python3 scripts/manifest_gen.py -p dswinir --pretty
```

## 路径约定

### 本地路径 (训练服务器)

| 类型 | 路径 | 说明 |
|------|------|------|
| 实验目录 | `experiments/<project>/` | 训练产物、checkpoints |
| 日志目录 | `tb_logger/<project>/` | TensorBoard 日志 |
| 结果目录 | `results/<project>/` | 测试结果、可视化 |

### 云端路径 (阿里云盘)

采用 `{project_id}-{name}` 命名，与 specs 目录保持一致：

```
/BasicOFR/
├── {project_id}-{name}/           # 例: 422-orcanet
│   ├── models/                     # Checkpoints
│   │   ├── iter_100000.pth
│   │   └── iter_200000.pth
│   ├── results/                    # 测试结果
│   │   ├── visual_comparison.png
│   │   └── metrics_export.csv
│   ├── tb_logger/                  # TensorBoard
│   │   └── events.out.tfevents.*
│   └── manifest.yaml               # 文件索引
└── shared/                         # 共享资源
    └── pretrained_models/
```

### Specs 状态文件映射

| specs 文件 | 云端对应 | 说明 |
|-----------|---------|------|
| `specs/{proj}/results/metrics.yaml` | - | 本地 SSOT，不上传 |
| `specs/{proj}/results/manifest.yaml` | 同步 | 云端文件索引 |
| `specs/{proj}/config/experiment_queue.yaml` | - | 本地调度配置 |

## 与 ofr-exp-train 集成

在 `complete-exp` 后触发上传：

```
complete-exp →
  1. 验证训练达到目标迭代
  2. 生成 manifest
  3. 确认上传（可选）
  4. 执行上传
  5. 更新 specs/{proj}/results/manifest.yaml
```

**推荐工作流**:
```bash
# 1. 标记实验完成
/ofr-exp-train complete-exp

# 2. 生成 manifest 并上传
python3 scripts/manifest_gen.py -p <project> -o specs/{proj}/results/ --pretty
python3 scripts/aliyunpan_ops.py upload-exp -p <project>

# 3. 更新 specs manifest（自动同步云端 URL）
python3 scripts/aliyunpan_ops.py update-manifest -p <project> \
  --specs-file specs/{proj}/results/manifest.yaml
```

## 与 ofr-exp-test 集成

测试完成后上传可视化产物：

```bash
# 1. 运行测试并收集结果
/ofr-exp-test run -p <project>

# 2. 上传可视化结果
python3 scripts/aliyunpan_ops.py upload-exp -p <project> --only results/

# 3. 更新 metrics.yaml 中的 artifacts 字段
python3 scripts/aliyunpan_ops.py update-artifacts -p <project> \
  --metrics-file specs/{proj}/results/metrics.yaml
```

## 与 ofr-paper-generate 集成

论文生成时通过 manifest 获取图像路径：

```python
# 读取 manifest 获取云端 URL
import yaml
with open('specs/{proj}/results/manifest.yaml') as f:
    manifest = yaml.safe_load(f)

# 生成图像引用
for file in manifest['files']['results']:
    print(f"云端路径: aliyunpan://BasicOFR/{proj}/{file['remote']}")
```

## Ideas 目录迁移 (#17)

将 Ideas/ 目录下的大文件（LaTeX 源码、原始代码）迁移到云端，减少 GitHub 仓库大小。

### upload-ideas - 上传 Ideas 大文件

```bash
# 上传单个项目
python3 scripts/aliyunpan_ops.py upload-ideas --project WaveMamba

# 上传所有项目
python3 scripts/aliyunpan_ops.py upload-ideas --all

# 预览模式
python3 scripts/aliyunpan_ops.py upload-ideas --project WaveMamba --dry-run
```

### 迁移后结构

**本地 (迁移后)**:
```
Ideas/{project}/
├── README.md            # 项目概述（保留）
├── manifest.yaml        # 云端索引（新增）
└── .gitkeep
# Latex/ 和 Codes/ 已移至云端
```

**云端**:
```
/BasicOFR/Ideas/
├── WaveMamba/
│   ├── Latex/           # LaTeX 源码
│   └── Codes/           # 原始代码
├── DepthPrompt/
│   ├── Latex/
│   └── Codes/
└── manifest.yaml        # 全局索引
```

### 生成 Ideas manifest

```bash
# 生成单个项目 manifest
python3 scripts/manifest_gen.py --ideas --project WaveMamba

# 生成所有项目 manifest
python3 scripts/manifest_gen.py --ideas --all
```

### 更新 .gitignore

迁移后自动添加排除规则：
```gitignore
# Ideas large files (migrated to aliyunpan)
Ideas/*/Latex/
Ideas/*/Codes/
!Ideas/*/manifest.yaml
```

## 参考

- **aliyunpan CLI 命令详情**: 见 `references/commands.md`
- **GitHub 项目**: https://github.com/tickstep/aliyunpan
