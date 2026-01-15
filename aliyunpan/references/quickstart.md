# Aliyunpan 快速入门指南

本指南介绍如何安装 aliyunpan CLI 并在 BasicOFR 项目中上传/下载实验数据。

## 1. 安装

### macOS (推荐使用 Homebrew)

```bash
brew install tickstep/tap/aliyunpan
```

### Linux

```bash
# 下载最新版本
wget https://github.com/tickstep/aliyunpan/releases/latest/download/aliyunpan-v0.3.2-linux-amd64.zip

# 解压并安装
unzip aliyunpan-v0.3.2-linux-amd64.zip
chmod +x aliyunpan
sudo mv aliyunpan /usr/local/bin/

# 验证安装
aliyunpan --version
```

### Windows

1. 从 [GitHub Releases](https://github.com/tickstep/aliyunpan/releases) 下载 `aliyunpan-vX.X.X-windows-amd64.zip`
2. 解压到任意目录
3. 将目录添加到系统 PATH 环境变量

## 2. 登录

```bash
# 登录阿里云盘（需扫码两次）
aliyunpan login

# 验证登录状态
aliyunpan who
```

**注意**：登录过程需要用手机阿里云盘 App 扫码确认两次。

## 3. 验证环境

使用 BasicOFR 提供的检查脚本：

```bash
python3 .claude/skills/aliyunpan/scripts/aliyunpan_ops.py check
```

输出示例：
```
🔍 检查 aliyunpan 状态...

✅ aliyunpan 已安装: /usr/local/bin/aliyunpan
✅ 已登录: user@example.com
```

## 4. 上传数据

### 上传实验产物

将 `experiments/`、`tb_logger/`、`results/` 目录上传到云端：

```bash
# 基本用法
python3 .claude/skills/aliyunpan/scripts/aliyunpan_ops.py upload-exp --project <project_name>

# 示例：上传 wavemamba 项目
python3 .claude/skills/aliyunpan/scripts/aliyunpan_ops.py upload-exp -p wavemamba

# 预览模式（不实际上传）
python3 .claude/skills/aliyunpan/scripts/aliyunpan_ops.py upload-exp -p wavemamba --dry-run
```

上传完成后，文件将存储在：
```
aliyunpan://BasicOFR/wavemamba/20260112_143000/
├── experiments/wavemamba/
├── tb_logger/wavemamba/
└── results/wavemamba/
```

### 上传 Ideas 大文件

```bash
# 上传单个项目
python3 .claude/skills/aliyunpan/scripts/aliyunpan_ops.py upload-ideas --project WaveMamba

# 上传所有 Ideas 项目
python3 .claude/skills/aliyunpan/scripts/aliyunpan_ops.py upload-ideas --all
```

### 上传论文图像

```bash
python3 .claude/skills/aliyunpan/scripts/aliyunpan_ops.py upload-papers --paper-id 001-wavemamba
```

### 原生 CLI 上传

```bash
# 上传单个文件
aliyunpan upload /local/path/file.pth /BasicOFR/project/models/

# 上传目录
aliyunpan upload /local/experiments/wavemamba/ /BasicOFR/wavemamba/

# 排除特定文件
aliyunpan upload -exn "^__pycache__$" -exn "\.pyc$" /local/path /cloud/path
```

## 5. 下载数据

### 下载实验产物

```bash
# 下载到 downloads/ 目录
python3 .claude/skills/aliyunpan/scripts/aliyunpan_ops.py download-exp --cloud /BasicOFR/wavemamba/

# 下载到指定目录
python3 .claude/skills/aliyunpan/scripts/aliyunpan_ops.py download-exp -c /BasicOFR/wavemamba/ -s /path/to/save

# 覆盖已存在文件
python3 .claude/skills/aliyunpan/scripts/aliyunpan_ops.py download-exp -c /BasicOFR/wavemamba/ --overwrite
```

### 原生 CLI 下载

```bash
# 下载到当前目录
aliyunpan download /BasicOFR/wavemamba/models/best.pth

# 下载到指定目录
aliyunpan download --saveto /local/models/ /BasicOFR/wavemamba/models/

# 覆盖已存在文件
aliyunpan download --ow --saveto /local/models/ /BasicOFR/wavemamba/models/
```

## 6. 同步目录

持续同步本地与云端目录：

```bash
# 上传模式（本地 → 云盘）
python3 .claude/skills/aliyunpan/scripts/aliyunpan_ops.py sync-exp --project wavemamba --mode upload

# 下载模式（云盘 → 本地）
python3 .claude/skills/aliyunpan/scripts/aliyunpan_ops.py sync-exp -p wavemamba -m download
```

## 7. 列出云端文件

```bash
# 列出根目录
python3 .claude/skills/aliyunpan/scripts/aliyunpan_ops.py list-cloud

# 列出项目目录
python3 .claude/skills/aliyunpan/scripts/aliyunpan_ops.py list-cloud /BasicOFR/wavemamba/

# 详细列表（显示大小、时间）
python3 .claude/skills/aliyunpan/scripts/aliyunpan_ops.py list-cloud /BasicOFR/ --detailed
```

## 8. 生成 Manifest

上传完成后生成文件索引：

```bash
# 更新 specs manifest
python3 .claude/skills/aliyunpan/scripts/aliyunpan_ops.py update-manifest --project wavemamba

# 生成 JSON manifest
python3 .claude/skills/aliyunpan/scripts/manifest_gen.py --project wavemamba --pretty
```

## 云端目录结构

BasicOFR 项目使用以下云端目录结构：

```
aliyunpan://BasicOFR/
├── experiments/
│   └── {project}/
│       ├── checkpoints/      # 模型权重 (.pth)
│       ├── tb_logger/        # TensorBoard 日志
│       └── results/          # 测试结果图像
├── papers/
│   └── {paper_id}/
│       └── figures/          # 论文高分辨率图像
└── ideas/
    └── {project}/
        ├── Latex/            # LaTeX 源码
        └── Codes/            # 原始代码
```

## 常见问题

### Q: 登录失败怎么办？

```bash
# 清除登录状态
aliyunpan logout

# 重新登录
aliyunpan login
```

### Q: 上传速度很慢？

```bash
# 调整并发数
aliyunpan config set -max_upload_parallel 10
```

### Q: 如何查看网盘剩余空间？

```bash
aliyunpan quota
```

### Q: 如何排除 Git 和缓存文件？

```bash
aliyunpan upload -exn "^\.git$" -exn "^__pycache__$" -exn "\.pyc$" /local/path /cloud/path
```

## 参考

- [aliyunpan GitHub](https://github.com/tickstep/aliyunpan)
- [命令参考](./commands.md)
- [SKILL.md](../SKILL.md)
