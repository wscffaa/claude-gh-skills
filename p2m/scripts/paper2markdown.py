#!/usr/bin/env python3
"""
将论文 PDF 转换为 Markdown（基于 marker-pdf）。

依赖：
  - pip install marker-pdf
  - pip install requests  # arXiv 下载用
  - codex CLI（用于生成规范的文件夹名称）

用法：
  python3 paper2markdown.py /path/to/paper.pdf
  python3 paper2markdown.py 2301.12345
  python3 paper2markdown.py arxiv:2301.12345
  python3 paper2markdown.py /path/to/paper.pdf --out-dir ./output --overwrite
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None


def _eprint(msg: str) -> None:
    sys.stderr.write(msg + "\n")


def _log(msg: str) -> None:
    if tqdm is not None:
        tqdm.write(msg, file=sys.stderr)
    else:
        _eprint(msg)


def _parse_arxiv_id(value: str) -> str | None:
    """解析 arXiv ID，支持 2301.12345、arxiv:2301.12345、2301.12345v2 格式"""
    m = re.match(r"^(arxiv:)?(\d{4}\.\d{4,5})(v\d+)?$", value.strip())
    if not m:
        return None
    arxiv_id = m.group(2)
    version = m.group(3) or ""
    return f"{arxiv_id}{version}"


def _download_arxiv_pdf(arxiv_id: str) -> Path:
    """下载 arXiv PDF 到临时目录"""
    if requests is None:
        raise RuntimeError("缺少依赖 requests：请先 pip install requests")

    url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    dest = Path(tempfile.gettempdir()) / f"arxiv_{arxiv_id}.pdf"

    _log(f"📥 下载 arXiv 论文: {url}")
    try:
        with requests.get(url, stream=True, timeout=(10, 120)) as resp:
            resp.raise_for_status()
            total = resp.headers.get("Content-Length")
            total_bytes = int(total) if total and total.isdigit() else None
            pbar = (
                tqdm(
                    total=total_bytes,
                    unit="B",
                    unit_scale=True,
                    unit_divisor=1024,
                    desc="Download",
                    file=sys.stderr,
                )
                if tqdm is not None
                else None
            )
            with dest.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)
                    if pbar is not None:
                        pbar.update(len(chunk))
            if pbar is not None:
                pbar.close()
        _log(f"✅ 下载完成: {dest}")
    except requests.exceptions.Timeout as exc:
        _cleanup_file(dest)
        raise RuntimeError(f"网络超时：{url}") from exc
    except requests.exceptions.RequestException as exc:
        _cleanup_file(dest)
        raise RuntimeError(f"下载失败：{url}（{exc}）") from exc
    except OSError as exc:
        _cleanup_file(dest)
        raise RuntimeError(f"写入失败：{dest}（{exc}）") from exc

    return dest


def _cleanup_file(path: Path) -> None:
    """安全删除文件"""
    try:
        if path and path.exists():
            path.unlink()
    except Exception:
        pass


def _fix_image_paths(md_content: str) -> str:
    """修复 markdown 中的图片路径，添加 ./ 前缀以兼容更多查看器"""
    # 匹配 ![alt](path) 格式，其中 path 不以 http/https/./ 开头
    # 例如 ![](_page_1_Figure_2.jpeg) -> ![](Paper/_page_1_Figure_2.jpeg)
    import re

    def fix_path(match):
        alt = match.group(1)
        path = match.group(2)
        # 跳过已经是绝对路径或 http(s) 链接的情况
        if path.startswith(('http://', 'https://', './', '../', '/')):
            return match.group(0)
        # 添加 ./ 前缀
        return f'![{alt}](./{path})'

    return re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', fix_path, md_content)


def _run_marker(pdf_path: Path, output_dir: Path, page_range: str | None = None) -> Path:
    """使用 marker_single 转换 PDF 为 Markdown"""
    _log(f"🔄 转换 PDF: {pdf_path.name}")

    # 确保输出目录存在
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "marker_single",
        "--output_format", "markdown",
        "--output_dir", str(output_dir),
    ]
    if page_range:
        cmd.extend(["--page_range", page_range])
    cmd.append(str(pdf_path))

    try:
        _log(f"🔧 执行命令: {' '.join(cmd)}")
        _log("📊 marker 处理进度（由 marker_single 输出）：")
        result = subprocess.run(cmd, timeout=1800)  # 30 分钟超时
        if result.returncode != 0:
            raise RuntimeError(f"marker_single 执行失败: {result.returncode}")
    except FileNotFoundError:
        raise RuntimeError("marker_single 命令未找到，请先安装: pip install marker-pdf")
    except subprocess.TimeoutExpired:
        raise RuntimeError("marker_single 执行超时（>30分钟）")

    # marker_single 输出结构: output_dir/pdf_stem/pdf_stem/pdf_stem.md
    # 或者: output_dir/pdf_stem/pdf_stem.md
    pdf_stem = pdf_path.stem
    possible_paths = [
        output_dir / pdf_stem / pdf_stem / f"{pdf_stem}.md",
        output_dir / pdf_stem / f"{pdf_stem}.md",
        output_dir / f"{pdf_stem}.md",
    ]

    for p in possible_paths:
        if p.exists():
            _log(f"✅ 转换完成: {p}")
            return p

    # 尝试查找任何 .md 文件
    md_files = list(output_dir.rglob("*.md"))
    if md_files:
        _log(f"✅ 转换完成: {md_files[0]}")
        return md_files[0]

    raise RuntimeError(f"转换后未找到 Markdown 文件，检查目录: {output_dir}")


def _generate_folder_name(md_content: str, fallback_name: str) -> str:
    """使用 Codex 生成符合规范的文件夹名称"""
    # 定位 codex skill 脚本
    codex_script = Path(__file__).parent.parent.parent / "codex" / "scripts" / "codex.py"

    if not codex_script.exists():
        _log(f"⚠️ Codex skill 脚本不存在: {codex_script}")
        return _extract_title_fallback(md_content, fallback_name)

    # 取前 3000 字符作为上下文
    content_preview = md_content[:3000]

    prompt = f'''根据以下论文内容，生成一个简短的 CamelCase 格式的文件夹名称。

要求：
1. 名称应该反映论文的核心方法或创新点（如方法名、架构名）
2. 使用 CamelCase 格式（如 MambaIR、WaveMamba、DefMamba、FreqMamba、WaveletMask）
3. 长度控制在 5-20 个字符
4. 只输出名称本身，不要任何解释、标点或换行

参考已有命名：DefMamba、FreqMamba、MambaIR、WaveletMask、WaveMamba、MemFlow、SCSA

论文内容：
{content_preview}

输出（仅一个 CamelCase 名称）：'''

    try:
        result = subprocess.run(
            [sys.executable, str(codex_script), prompt, "-r", "low"],
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0 and result.stdout.strip():
            # 解析输出，取第一个有效的 CamelCase 名称
            output = result.stdout.strip()
            # 尝试从输出中提取符合格式的名称
            for line in output.split('\n'):
                line = line.strip()
                # 跳过空行和解释性文字
                if not line or line.startswith('#') or ':' in line:
                    continue
                # 清理可能的标点
                name = re.sub(r'[^\w]', '', line)
                # 验证名称格式
                if re.match(r'^[A-Z][a-zA-Z0-9]{2,25}$', name):
                    _log(f"📝 Codex 生成名称: {name}")
                    return name

            _log(f"⚠️ Codex 输出无效: {output[:100]}，使用 fallback")
    except FileNotFoundError:
        _log("⚠️ codex 脚本未找到，使用 fallback 名称")
    except subprocess.TimeoutExpired:
        _log("⚠️ codex 执行超时，使用 fallback 名称")
    except Exception as e:
        _log(f"⚠️ codex 执行失败: {e}，使用 fallback 名称")

    # Fallback: 从 markdown 提取标题
    return _extract_title_fallback(md_content, fallback_name)


def _extract_title_fallback(md_content: str, fallback_name: str) -> str:
    """从 markdown 内容提取标题作为 fallback"""
    # 尝试匹配 # 标题
    match = re.search(r'^#\s+(.+)$', md_content, re.MULTILINE)
    if match:
        title = match.group(1).strip()
        # 提取关键词生成 CamelCase
        words = re.findall(r'[A-Z][a-z]+|[A-Z]+(?=[A-Z]|$)|[a-z]+', title)
        if words:
            # 取前 3 个有意义的词
            meaningful = [w for w in words if len(w) > 2][:3]
            if meaningful:
                name = ''.join(w.capitalize() for w in meaningful)
                if 3 <= len(name) <= 25:
                    return name

    # 最终 fallback
    return _sanitize_name(fallback_name)


def _sanitize_name(name: str) -> str:
    """清理名称，移除特殊字符"""
    # 移除版本号
    name = re.sub(r'v\d+$', '', name)
    # 移除非字母数字字符
    name = re.sub(r'[^a-zA-Z0-9]', '', name)
    # 确保首字母大写
    if name and name[0].islower():
        name = name[0].upper() + name[1:]
    return name[:25] if name else "Paper"


def _copy_tree_with_progress(src_dir: Path, dest_dir: Path) -> int:
    """递归复制目录，并在文件级别显示进度条。"""
    if not src_dir.exists():
        return 0

    dest_dir.mkdir(parents=True, exist_ok=True)
    files = [p for p in src_dir.rglob("*") if p.is_file()]
    pbar = (
        tqdm(total=len(files), desc=f"Copy {src_dir.name}", unit="file", file=sys.stderr)
        if tqdm is not None and files
        else None
    )

    copied = 0
    for file_path in files:
        rel = file_path.relative_to(src_dir)
        target = dest_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, target)
        copied += 1
        if pbar is not None:
            pbar.update(1)

    if pbar is not None:
        pbar.close()
    return copied


# ============== Phase 2: 代码下载相关函数 ==============

def _extract_github_url(md_content: str) -> str | None:
    """从论文摘要部分提取 GitHub URL"""
    # 聚焦前 3000 字符（通常包含 Abstract）
    abstract_region = md_content[:3000]

    # 匹配 GitHub URL
    match = re.search(r'https?://github\.com/([\w.-]+)/([\w.-]+)', abstract_region)
    if match:
        url = f"https://github.com/{match.group(1)}/{match.group(2)}"
        # 清理 URL 末尾可能的标点
        url = re.sub(r'[.,;:)\]]+$', '', url)
        return url

    return None


def _get_repo_tree(github_url: str) -> str | None:
    """获取仓库 Python 文件列表"""
    if requests is None:
        _log("⚠️ 缺少 requests 库，无法获取仓库结构")
        return None

    match = re.match(r'https?://github\.com/([\w.-]+)/([\w.-]+)', github_url)
    if not match:
        return None

    owner, repo = match.groups()

    # 尝试 main 和 master 分支
    for branch in ['main', 'master']:
        api_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
        try:
            resp = requests.get(api_url, timeout=30)
            if resp.status_code == 200:
                tree = resp.json().get('tree', [])
                # 只保留 .py 文件，排除无关目录
                py_files = [
                    item['path'] for item in tree
                    if item['path'].endswith('.py')
                    and not any(x in item['path'].lower() for x in
                               ['test/', 'tests/', 'docs/', 'examples/', 'scripts/', 'demo/'])
                ]
                if py_files:
                    return '\n'.join(py_files)
        except Exception as e:
            _log(f"⚠️ 获取仓库结构失败 ({branch}): {e}")
            continue

    return None


def _identify_core_files(repo_tree: str, paper_title: str) -> list[str]:
    """使用 Codex 识别核心架构文件"""
    # 定位 codex skill 脚本
    codex_script = Path(__file__).parent.parent.parent / "codex" / "scripts" / "codex.py"

    if not codex_script.exists():
        _log(f"⚠️ Codex skill 脚本不存在: {codex_script}")
        return _fallback_identify_core_files(repo_tree)

    prompt = f'''你是深度学习代码分析专家。分析以下 GitHub 仓库结构，识别实现论文核心创新的架构文件。

## 论文标题
{paper_title}

## 仓库 Python 文件列表
{repo_tree}

## 任务
识别 1-3 个核心网络架构文件，这些文件应该：
1. 包含 nn.Module 子类定义
2. 实现论文的核心创新（如新的注意力机制、网络结构）
3. 文件名通常包含 arch、net、model、network、former、mamba 等关键词

## 排除
- __init__.py
- train.py, test.py, inference.py（训练/测试脚本）
- losses.py, loss.py, metrics.py（损失/指标）
- data*.py, dataset*.py（数据加载）
- utils.py, tools.py, helpers.py（工具函数）
- base_model.py, base_arch.py（基类）
- options/, configs/（配置文件）

## 输出格式
只输出文件路径，每行一个，不要任何解释或额外文字：'''

    try:
        result = subprocess.run(
            [sys.executable, str(codex_script), prompt, "-r", "low"],
            capture_output=True,
            text=True,
            timeout=120
        )

        if result.returncode == 0 and result.stdout.strip():
            # 解析输出，提取有效路径
            lines = result.stdout.strip().split('\n')
            paths = []
            for line in lines:
                line = line.strip()
                # 过滤有效的 Python 文件路径
                if (line.endswith('.py')
                    and '/' in line
                    and not line.startswith('#')
                    and not line.startswith('-')
                    and '__init__' not in line):
                    paths.append(line)

            if paths:
                _log(f"🤖 Codex 识别到 {len(paths[:3])} 个核心文件")
                return paths[:3]

        _log("⚠️ Codex 未返回有效结果，使用规则匹配")
    except subprocess.TimeoutExpired:
        _log("⚠️ Codex 执行超时，使用规则匹配")
    except Exception as e:
        _log(f"⚠️ Codex 执行失败: {e}，使用规则匹配")

    return _fallback_identify_core_files(repo_tree)


def _fallback_identify_core_files(repo_tree: str) -> list[str]:
    """规则匹配识别核心文件（fallback）"""
    files = repo_tree.strip().split('\n')

    # 优先级关键词
    priority_keywords = ['arch', 'network', 'net', 'model', 'former', 'mamba', 'attention']
    exclude_keywords = ['__init__', 'base', 'utils', 'tools', 'train', 'test', 'loss',
                       'data', 'config', 'option', 'inference', 'demo']

    candidates = []
    for f in files:
        f_lower = f.lower()
        # 排除不需要的文件
        if any(ex in f_lower for ex in exclude_keywords):
            continue
        # 检查优先级关键词
        score = sum(1 for kw in priority_keywords if kw in f_lower)
        if score > 0:
            candidates.append((score, f))

    # 按分数排序，取前 3 个
    candidates.sort(key=lambda x: -x[0])
    result = [f for _, f in candidates[:3]]

    if result:
        _log(f"📋 规则匹配识别到 {len(result)} 个核心文件")

    return result


def _download_code_files(github_url: str, file_paths: list[str], dest_dir: Path) -> tuple[list[str], dict[str, str]]:
    """下载代码文件到目标目录

    Returns:
        (已下载的文件名列表, 文件名到原始路径的映射)
    """
    if requests is None:
        _log("⚠️ 缺少 requests 库，无法下载代码")
        return [], {}

    match = re.match(r'https?://github\.com/([\w.-]+)/([\w.-]+)', github_url)
    if not match:
        return [], {}

    owner, repo = match.groups()
    dest_dir.mkdir(parents=True, exist_ok=True)

    downloaded = []
    file_mapping = {}  # 记录原始路径到文件名的映射

    for file_path in file_paths:
        # 尝试 main 和 master 分支
        success = False
        for branch in ['main', 'master']:
            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{file_path}"
            try:
                resp = requests.get(raw_url, timeout=30)
                if resp.status_code == 200:
                    filename = Path(file_path).name
                    dest_file = dest_dir / filename
                    dest_file.write_text(resp.text, encoding='utf-8')
                    downloaded.append(filename)
                    file_mapping[filename] = file_path
                    _log(f"✅ 下载: {filename}")
                    success = True
                    break
            except Exception as e:
                continue

        if not success:
            _log(f"⚠️ 下载失败: {file_path}")

    # 保存来源元数据
    if downloaded:
        meta = {
            "source": github_url,
            "branch": "main",  # 默认记录 main
            "files": file_mapping,
            "downloaded_at": datetime.now().isoformat()
        }
        meta_file = dest_dir / "_source.json"
        meta_file.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding='utf-8')
        _log(f"📝 保存元数据: {meta_file.name}")

    return downloaded, file_mapping


def _parse_local_imports(code_content: str, repo_files: set[str]) -> list[str]:
    """解析代码中的本地导入，返回需要下载的文件路径

    Args:
        code_content: Python 代码内容
        repo_files: 仓库中所有 Python 文件路径的集合

    Returns:
        需要下载的文件路径列表
    """
    imports = set()

    # 匹配 from xxx import yyy 和 import xxx
    # 例如: from models.GBC import GBC -> models/GBC.py
    # 例如: from mmcv.cnn.bricks.transformer import build_dropout -> mmcv/cnn/bricks/transformer.py
    patterns = [
        r'^from\s+([\w.]+)\s+import',  # from xxx import
        r'^import\s+([\w.]+)',          # import xxx
    ]

    for line in code_content.split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue

        for pattern in patterns:
            match = re.match(pattern, line)
            if match:
                module_path = match.group(1)
                # 转换为文件路径: models.GBC -> models/GBC.py
                file_path = module_path.replace('.', '/') + '.py'
                imports.add(file_path)
                # 也尝试 __init__.py
                init_path = module_path.replace('.', '/') + '/__init__.py'
                imports.add(init_path)
                break

    # 过滤：只保留仓库中存在的文件
    result = []
    for imp in imports:
        # 检查完整路径或部分匹配
        for repo_file in repo_files:
            if repo_file.endswith(imp) or imp in repo_file:
                result.append(repo_file)
                break

    return list(set(result))


def _resolve_dependencies(
    dest_dir: Path,
    github_url: str,
    repo_tree: str,
    downloaded: list[str],
    file_mapping: dict[str, str],
    max_depth: int = 2
) -> tuple[list[str], dict[str, str]]:
    """递归解析并下载依赖

    Args:
        dest_dir: 目标目录
        github_url: GitHub 仓库 URL
        repo_tree: 仓库文件树（换行分隔的路径）
        downloaded: 已下载的文件名列表
        file_mapping: 文件名到原始路径的映射
        max_depth: 最大递归深度

    Returns:
        (更新后的 downloaded 列表, 更新后的 file_mapping)
    """
    if max_depth <= 0:
        return downloaded, file_mapping

    # 构建仓库文件集合
    repo_files = set(repo_tree.strip().split('\n'))

    # 解析 GitHub URL
    match = re.match(r'https?://github\.com/([\w.-]+)/([\w.-]+)', github_url)
    if not match:
        return downloaded, file_mapping

    owner, repo = match.groups()

    # 遍历已下载的文件，解析依赖
    new_deps = []
    for filename in downloaded:
        file_path = dest_dir / filename
        if not file_path.exists() or not filename.endswith('.py'):
            continue

        try:
            code_content = file_path.read_text(encoding='utf-8')
        except Exception:
            continue

        # 解析本地导入
        deps = _parse_local_imports(code_content, repo_files)
        for dep in deps:
            dep_name = Path(dep).name
            # 跳过已下载的
            if dep_name in downloaded or dep_name in [Path(d).name for d in new_deps]:
                continue
            # 跳过 __init__.py
            if dep_name == '__init__.py':
                continue
            new_deps.append(dep)

    if not new_deps:
        return downloaded, file_mapping

    _log(f"🔗 发现 {len(new_deps)} 个依赖文件")

    # 下载新依赖
    new_downloaded = []
    for dep_path in new_deps:
        for branch in ['main', 'master']:
            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{dep_path}"
            try:
                resp = requests.get(raw_url, timeout=30)
                if resp.status_code == 200:
                    filename = Path(dep_path).name
                    dest_file = dest_dir / filename
                    dest_file.write_text(resp.text, encoding='utf-8')
                    new_downloaded.append(filename)
                    file_mapping[filename] = dep_path
                    _log(f"  ✅ 依赖: {filename}")
                    break
            except Exception:
                continue

    # 更新已下载列表
    downloaded = downloaded + new_downloaded

    # 递归处理新下载的依赖
    if new_downloaded and max_depth > 1:
        return _resolve_dependencies(
            dest_dir, github_url, repo_tree,
            downloaded, file_mapping, max_depth - 1
        )

    return downloaded, file_mapping


# ============== 代码本地化（Codex 处理）==============

def _codex_localize_code(codes_dir: Path, downloaded: list[str], paper_name: str) -> None:
    """使用 Codex 本地化代码，移除 mmcv/mmengine 等大型框架依赖

    Args:
        codes_dir: 代码目录
        downloaded: 已下载的文件名列表
        paper_name: 论文/项目名称
    """
    if not downloaded:
        return

    # 定位 codex skill 脚本
    codex_script = Path(__file__).parent.parent.parent / "codex" / "scripts" / "codex.py"
    if not codex_script.exists():
        _log("⚠️ Codex skill 不可用，跳过代码本地化")
        return

    # 构建文件列表
    py_files = [f for f in downloaded if f.endswith('.py')]
    if not py_files:
        return

    files_list = '\n'.join(f'- {codes_dir / f}' for f in py_files)

    prompt = f'''你是深度学习代码迁移专家。请本地化以下代码文件，使其不依赖 mmcv/mmengine/mmcls 等大型框架。

## 目标项目
{paper_name}

## 待处理文件
{files_list}

## 本地化原则

1. **移除大型框架依赖**：
   - mmcv → 用 PyTorch/timm 原生实现替代
   - mmengine → 用 PyTorch 原生实现替代
   - mmcls/mmseg/mmdet → 移除注册器，直接使用类

2. **保留允许的依赖**：
   - torch, torchvision（核心）
   - timm（预训练模型、DropPath 等）
   - einops（张量操作）
   - mamba_ssm, selective_scan_cuda（Mamba 相关）
   - basicsr（训练框架）

3. **替换策略**：
   - `from mmcv.runner import BaseModule` → 继承 `nn.Module`，添加 `init_cfg` 参数
   - `from mmcv.cnn import build_norm_layer` → 直接用 `nn.LayerNorm/BatchNorm2d`
   - `from mmcv.cnn.bricks.transformer import build_dropout` → 用 `timm.models.layers.DropPath`
   - `@BACKBONES.register_module()` → 移除装饰器（或改用 basicsr 的 @ARCH_REGISTRY.register()）
   - `from mmcv.utils import to_2tuple` → 从 timm 导入或自己实现

4. **代码质量要求**：
   - 保持原有功能不变
   - 添加必要的导入语句
   - 如需辅助函数，创建 _utils.py
   - 生成 __init__.py 导出主要类

## 输出要求
直接修改文件，不要输出解释。确保修改后的代码可以正常导入。
'''

    try:
        _log("🤖 Codex 正在本地化代码...")
        result = subprocess.run(
            [sys.executable, str(codex_script), prompt, "-r", "high"],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(codes_dir.parent.parent)  # 在 Ideas/<project> 目录下运行
        )

        if result.returncode == 0:
            _log("✅ 代码本地化完成")
        else:
            _log(f"⚠️ Codex 返回非零状态: {result.returncode}")
            if result.stderr:
                _log(f"   {result.stderr[:200]}")

    except subprocess.TimeoutExpired:
        _log("⚠️ Codex 执行超时（5分钟），跳过本地化")
    except Exception as e:
        _log(f"⚠️ Codex 执行失败: {e}")


def _codex_normalize_code(codes_dir: Path, folder_name: str, paper_content: str) -> bool:
    """使用 Codex 将原始代码规范化，保存到 Codes/ 目录

    参考 Ideas/DefMamba/DefMamba.py 的规范格式：
    - 移除 mmcv/mmengine 等大型框架依赖
    - 添加中文注释说明核心逻辑
    - 生成 <Name>.py + utils.py + __init__.py

    Args:
        codes_dir: 代码目录 (Codes/)
        folder_name: 项目名称（如 DefMamba）
        paper_content: 论文 markdown 内容

    Returns:
        是否成功生成规范化代码
    """
    if not codes_dir.exists():
        return False

    # 收集所有 Python 文件
    py_files = [f for f in codes_dir.glob("*.py") if not f.name.startswith('_')]
    if not py_files:
        return False

    # 定位 codex skill 脚本
    codex_script = Path(__file__).parent.parent.parent / "codex" / "scripts" / "codex.py"
    if not codex_script.exists():
        _log("⚠️ Codex skill 不可用，跳过规范化代码生成")
        return False

    # 读取原始代码内容
    code_contents = {}
    for py_file in py_files:
        try:
            code_contents[py_file.name] = py_file.read_text(encoding='utf-8')
        except Exception:
            continue

    if not code_contents:
        return False

    # 构建文件列表字符串（限制长度）
    files_info = '\n'.join([f"### {codes_dir}/{name}\n```python\n{content[:4000]}\n```"
                           for name, content in list(code_contents.items())[:3]])

    prompt = f'''你是深度学习代码规范化专家。请将以下原始代码转换为规范格式。

## 项目名称
{folder_name}

## 论文摘要（用于理解核心创新）
{paper_content[:2500]}

## 原始代码文件
{files_info}

## 规范化要求

### 1. 依赖处理
移除以下大型框架依赖，替换为 PyTorch/timm 原生实现：
- mmcv, mmengine, mmdet, mmseg, mmcls → 移除
- `from mmcv.runner import BaseModule` → 继承 `nn.Module`
- `from mmcv.cnn import build_norm_layer` → 使用 `nn.LayerNorm/BatchNorm2d`
- `@BACKBONES.register_module()` → 移除装饰器
- `from mmcv.utils import to_2tuple` → 从 timm 导入或自己实现

保留允许的依赖：
- torch, torchvision
- timm (DropPath, trunc_normal_ 等)
- einops
- mamba_ssm, selective_scan_cuda

### 2. 代码结构（参考 Ideas/DefMamba/DefMamba.py）
```python
"""
{folder_name} 主体网络

核心流程：
1) xxx：功能描述
2) yyy：功能描述
"""
import torch
import torch.nn as nn
from timm.layers import DropPath, trunc_normal_
from einops import rearrange

class CoreModule(nn.Module):
    """核心模块说明（中文）"""
    def __init__(self, ...):
        super().__init__()
        # 初始化说明

    def forward(self, x):
        # (B, C, H, W) -> (B, C, H, W)
        return x
```

### 3. 输出文件（保存到 {codes_dir}）

1. **{codes_dir}/{folder_name}.py** - 主架构文件
   - 包含核心网络类
   - 每个类和重要方法有中文注释
   - 张量维度在注释中标明

2. **{codes_dir}/utils.py** - 工具函数（如有需要）
   - SelectiveScan、辅助函数等

3. **{codes_dir}/__init__.py** - 模块导出
   ```python
   """
   {folder_name} - 核心创新点简述

   核心模块：
   - MainClass: 主网络
   - HelperClass: 辅助模块
   """
   from .{folder_name} import MainClass, HelperClass
   __all__ = ['MainClass', 'HelperClass']
   ```

## 输出要求
直接修改/生成文件到 {codes_dir} 目录，不要输出解释。确保代码可以正常导入。
'''

    try:
        _log("🤖 Codex 正在规范化代码...")
        result = subprocess.run(
            [sys.executable, str(codex_script), prompt, "-r", "high"],
            capture_output=True,
            text=True,
            timeout=600,  # 10 分钟超时
            cwd=str(codes_dir.parent)  # 在 Ideas/<Name>/ 目录下运行
        )

        if result.returncode == 0:
            # 检查是否生成了规范化文件
            main_file = codes_dir / f"{folder_name}.py"
            init_file = codes_dir / "__init__.py"
            if main_file.exists() or init_file.exists():
                _log(f"✅ 规范化代码生成完成: {codes_dir}")
                return True
            else:
                _log("⚠️ Codex 未生成预期文件")
        else:
            _log(f"⚠️ Codex 返回非零状态: {result.returncode}")
            if result.stderr:
                _log(f"   {result.stderr[:300]}")

    except subprocess.TimeoutExpired:
        _log("⚠️ Codex 执行超时（10分钟），跳过规范化")
    except Exception as e:
        _log(f"⚠️ Codex 执行失败: {e}")

    return False


def _check_existing_paper(base_dir: Path, folder_name: str = None) -> tuple[Path | None, str | None]:
    """检查是否已存在转换好的论文 markdown

    Args:
        base_dir: Ideas 基目录
        folder_name: 可选的指定文件夹名

    Returns:
        (md_path, folder_name) 如果存在，否则 (None, None)
    """
    if folder_name:
        paper_dir = base_dir / folder_name / "Paper"
        if paper_dir.exists():
            md_files = list(paper_dir.glob("*.md"))
            if md_files:
                return md_files[0], folder_name
    return None, None


def _find_existing_project(base_dir: Path, arxiv_id: str = None, pdf_stem: str = None) -> tuple[Path | None, str | None]:
    """根据 arXiv ID 或 PDF 文件名查找已存在的项目

    Args:
        base_dir: Ideas 基目录
        arxiv_id: arXiv ID（如 2501.04486）
        pdf_stem: PDF 文件名（不含扩展名）

    Returns:
        (md_path, folder_name) 如果存在，否则 (None, None)
    """
    if not base_dir.exists():
        return None, None

    # 遍历 Ideas 下的所有子目录
    for project_dir in base_dir.iterdir():
        if not project_dir.is_dir():
            continue

        paper_dir = project_dir / "Paper"
        if not paper_dir.exists():
            continue

        # 检查是否有 md 文件
        md_files = list(paper_dir.glob("*.md"))
        if not md_files:
            continue

        # 检查 md 内容是否匹配 arXiv ID
        if arxiv_id:
            for md_file in md_files:
                try:
                    content = md_file.read_text(encoding='utf-8')[:2000]
                    if arxiv_id in content or f"arxiv.org/abs/{arxiv_id}" in content.lower():
                        return md_file, project_dir.name
                except Exception:
                    continue

        # 检查文件名是否匹配
        if pdf_stem:
            pdf_stem_lower = pdf_stem.lower().replace('_', '').replace('-', '')
            for md_file in md_files:
                md_stem = md_file.stem.lower().replace('_', '').replace('-', '')
                if pdf_stem_lower in md_stem or md_stem in pdf_stem_lower:
                    return md_file, project_dir.name

    return None, None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert paper PDF to Markdown via marker-pdf"
    )
    parser.add_argument(
        "input",
        help="论文 PDF 路径 或 arXiv ID（如 2301.12345 / arxiv:2301.12345）"
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="输出基目录（默认：./Ideas/）"
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖已存在的输出文件"
    )
    parser.add_argument(
        "--page-range",
        default=None,
        help="页面范围（如 0-9 表示前 10 页）"
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="禁用 tqdm 进度条（默认开启）"
    )
    parser.add_argument(
        "--github",
        default=None,
        help="GitHub 仓库地址（如未指定，自动从论文摘要提取）"
    )
    parser.add_argument(
        "--no-code",
        action="store_true",
        help="跳过代码下载，仅转换论文"
    )
    parser.add_argument(
        "--code-only",
        action="store_true",
        help="仅提取代码（跳过论文转换，要求论文已存在）"
    )
    args = parser.parse_args()

    tmp_pdf_path = None
    tmp_marker_dir = None
    global tqdm
    if args.no_progress:
        tqdm = None

    try:
        # 0. 确定输出目录
        if args.out_dir:
            base_dir = Path(args.out_dir).expanduser().resolve()
        else:
            base_dir = Path("Ideas").resolve()

        # 1. 解析输入
        arxiv_id = _parse_arxiv_id(str(args.input).strip())
        if arxiv_id:
            fallback_name = arxiv_id
            pdf_stem = None
        else:
            pdf_path = Path(args.input).expanduser().resolve()
            if not pdf_path.exists():
                _log(f"❌ PDF 不存在：{pdf_path}")
                return 2
            if pdf_path.suffix.lower() != ".pdf":
                _log(f"❌ 不是 PDF：{pdf_path}")
                return 2
            fallback_name = pdf_path.stem
            pdf_stem = pdf_path.stem

        # 2. 检查是否已存在转换好的论文
        existing_md, existing_folder = _find_existing_project(base_dir, arxiv_id, pdf_stem if not arxiv_id else None)

        if existing_md and not args.overwrite:
            _log(f"📄 检测到已存在的论文: {existing_md}")
            _log(f"📁 项目目录: {existing_folder}")

            # 读取已有的 markdown 内容用于代码提取
            md_content = existing_md.read_text(encoding="utf-8")
            folder_name = existing_folder
            final_file = existing_md

            # 如果指定了 --code-only 或论文已存在，直接跳到代码提取
            _log("⏭️ 跳过论文转换，直接进入代码提取阶段")

        else:
            # 需要转换论文
            if args.code_only:
                _log(f"❌ --code-only 模式但未找到已存在的论文")
                return 2

            # 下载 PDF（如果是 arXiv）
            if arxiv_id:
                tmp_pdf_path = _download_arxiv_pdf(arxiv_id)
                pdf_path = tmp_pdf_path

            # 创建临时目录运行 marker
            tmp_marker_dir = Path(tempfile.mkdtemp(prefix="p2m_marker_"))
            md_path = _run_marker(pdf_path, tmp_marker_dir, args.page_range)

            # 读取 markdown 内容
            md_content = md_path.read_text(encoding="utf-8")

            # 生成规范的文件夹名称
            folder_name = _generate_folder_name(md_content, fallback_name)
            _log(f"📁 最终文件夹名称: {folder_name}")

            # 输出到 Ideas/<PaperName>/Paper/ 子目录
            final_dir = base_dir / folder_name / "Paper"

            # 检查是否已存在
            if final_dir.exists() and not args.overwrite:
                _log(f"❌ 输出目录已存在（可用 --overwrite 覆盖）：{final_dir}")
                return 1

            # 移动文件到最终位置
            if final_dir.exists() and args.overwrite:
                shutil.rmtree(final_dir)
            final_dir.mkdir(parents=True, exist_ok=True)

            # 复制 md 同目录下的所有文件（包括图片）
            md_parent = md_path.parent
            items = list(md_parent.iterdir())
            pbar = (
                tqdm(total=len(items), desc="Collect output", unit="item", file=sys.stderr)
                if tqdm is not None and items
                else None
            )
            copied_files = []
            for item in items:
                if item.is_file():
                    if item.suffix.lower() == ".md":
                        # md 文件重命名，并修复图片路径
                        dest = final_dir / f"{folder_name}.md"
                        md_text = item.read_text(encoding='utf-8')
                        md_text = _fix_image_paths(md_text)
                        dest.write_text(md_text, encoding='utf-8')
                    else:
                        # 其他文件（图片等）保持原名
                        dest = final_dir / item.name
                        shutil.copy2(item, dest)
                    copied_files.append(dest.name)
                elif item.is_dir():
                    # 复制子目录（如 _images 目录）
                    dest_dir = final_dir / item.name
                    if tqdm is not None:
                        copied = _copy_tree_with_progress(item, dest_dir)
                        copied_files.append(f"{item.name}/ ({copied} files)")
                    else:
                        shutil.copytree(item, dest_dir)
                        copied_files.append(f"{item.name}/")
                if pbar is not None:
                    pbar.update(1)
            if pbar is not None:
                pbar.close()

            if len(copied_files) > 1:
                _log(f"📦 复制了 {len(copied_files)} 个文件/目录")

            final_file = final_dir / f"{folder_name}.md"
            _log(f"✅ 论文保存: {final_file}")

        # ============== Phase 2: 代码下载与规范化 ==============
        if not args.no_code:
            # 获取 GitHub URL
            github_url = args.github or _extract_github_url(md_content)

            if github_url:
                _log(f"🔗 检测到 GitHub: {github_url}")

                # 获取仓库结构
                _log("📂 获取仓库结构...")
                repo_tree = _get_repo_tree(github_url)

                if repo_tree:
                    # Codex 识别核心文件
                    _log("🤖 Codex 分析核心文件...")
                    core_files = _identify_core_files(repo_tree, folder_name)

                    if core_files:
                        codes_dir = base_dir / folder_name / "Codes"
                        downloaded, file_mapping = _download_code_files(github_url, core_files, codes_dir)
                        if downloaded:
                            # Phase 2.5: 解析并下载依赖
                            _log("🔍 解析代码依赖...")
                            downloaded, file_mapping = _resolve_dependencies(
                                codes_dir, github_url, repo_tree,
                                downloaded, file_mapping, max_depth=2
                            )

                            # Phase 3: 规范化代码（Codex 处理）
                            _log("🔧 规范化代码...")
                            _codex_normalize_code(codes_dir, folder_name, md_content)

                            # 更新元数据（包含依赖）
                            meta = {
                                "source": github_url,
                                "branch": "main",
                                "files": file_mapping,
                                "downloaded_at": datetime.now().isoformat()
                            }
                            meta_file = codes_dir / "_source.json"
                            meta_file.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding='utf-8')
                            _log(f"✅ 代码保存: {codes_dir} ({len(downloaded)} 文件)")
                        else:
                            _log("⚠️ 未能下载任何代码文件")
                    else:
                        _log("⚠️ 未识别到核心架构文件")
                else:
                    _log("⚠️ 无法获取仓库结构（可能是私有仓库或网络问题）")
            else:
                _log("ℹ️ 未检测到 GitHub URL（可用 --github 指定）")

        print(f"✅ 输出文件：{final_file}")
        return 0

    except Exception as e:
        _log(f"❌ 错误：{e}")
        return 1

    finally:
        # 清理临时文件
        _cleanup_file(tmp_pdf_path)
        if tmp_marker_dir and tmp_marker_dir.exists():
            try:
                shutil.rmtree(tmp_marker_dir)
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
