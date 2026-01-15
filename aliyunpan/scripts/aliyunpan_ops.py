#!/usr/bin/env python3
"""
aliyunpan 实验文件操作封装

功能：
- upload-exp: 上传实验产物到云盘
- download-exp: 从云盘下载实验产物
- sync-exp: 同步本地与云盘
- list-cloud: 列出云盘文件
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional


DEFAULT_EXCLUDES = [
    r"^\.git$",
    r"^__pycache__$",
    r"\.pyc$",
    r"^\.DS_Store$",
    r"^\.env$",
    r"^credentials",
    r"^\.idea$",
    r"^\.vscode$",
    r"^node_modules$",
]

EXPERIMENT_DIRS = ["experiments", "tb_logger", "results"]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _parse_progress_percent(line: str) -> Optional[float]:
    m = re.search(r"(?P<pct>\d{1,3}(?:\.\d+)?)%", line)
    if m:
        try:
            return float(m.group("pct"))
        except ValueError:
            return None

    m = re.search(
        r"[↑↓]\s*(?P<done>\d+(?:\.\d+)?)(?P<done_unit>[KMGTP]?B)\s*/\s*(?P<total>\d+(?:\.\d+)?)(?P<total_unit>[KMGTP]?B)",
        line,
    )
    if not m:
        return None

    unit_mul = {
        "B": 1,
        "KB": 1024,
        "MB": 1024**2,
        "GB": 1024**3,
        "TB": 1024**4,
        "PB": 1024**5,
    }
    try:
        done = float(m.group("done")) * unit_mul[m.group("done_unit").upper()]
        total = float(m.group("total")) * unit_mul[m.group("total_unit").upper()]
    except (KeyError, ValueError):
        return None
    if total <= 0:
        return None
    return max(0.0, min(100.0, done / total * 100.0))


def _run_cmd(cmd: List[str], dry_run: bool = False) -> int:
    cmd_str = " ".join(cmd)
    if dry_run:
        print(f"[DRY-RUN] {cmd_str}")
        return 0
    print(f"[EXEC] {cmd_str}")
    result = subprocess.run(cmd, capture_output=False)
    return result.returncode


def _run_cmd_with_progress(
    cmd: List[str],
    dry_run: bool = False,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> int:
    """带进度显示的命令执行"""
    cmd_str = " ".join(cmd)
    if dry_run:
        print(f"[DRY-RUN] {cmd_str}")
        return 0

    print(f"[EXEC] {cmd_str}")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    last_pct: Optional[float] = None
    assert proc.stdout is not None
    for raw in proc.stdout:
        for line in raw.replace("\r", "\n").splitlines():
            pct = _parse_progress_percent(line)
            if pct is not None and progress_callback:
                if last_pct is None or pct >= 100.0 or abs(pct - last_pct) >= 0.5:
                    progress_callback(f"{pct:.1f}%")
                    last_pct = pct
                continue
            print(line)

    return proc.wait()


def _build_exclude_args(excludes: List[str]) -> List[str]:
    args = []
    for pattern in excludes:
        args.extend(["-exn", pattern])
    return args


def cmd_upload(
    project: str,
    timestamp: Optional[str] = None,
    cloud_base: str = "/BasicOFR",
    dry_run: bool = False,
    extra_excludes: Optional[List[str]] = None,
) -> int:
    """上传实验产物到云盘"""
    ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    cloud_dir = f"{cloud_base}/{project}/{ts}"

    repo = _repo_root()
    local_paths = []
    for dirname in EXPERIMENT_DIRS:
        p = repo / dirname / project
        if p.is_dir():
            local_paths.append(str(p))

    if not local_paths:
        print(f"错误: 未找到项目 '{project}' 的任何实验目录")
        print(f"  检查: {', '.join(f'{d}/{project}' for d in EXPERIMENT_DIRS)}")
        return 1

    excludes = DEFAULT_EXCLUDES + (extra_excludes or [])
    exclude_args = _build_exclude_args(excludes)

    print(f"\n📤 上传实验产物: {project}")
    print(f"   目标云盘: {cloud_dir}")
    print(f"   本地目录: {local_paths}")
    print()

    for local_path in local_paths:
        cmd = ["aliyunpan", "upload"] + exclude_args + [local_path, cloud_dir]
        ret = _run_cmd_with_progress(
            cmd,
            dry_run,
            progress_callback=lambda p, lp=local_path: print(
                f"\r⏫ {Path(lp).name}: {p}", end="", flush=True
            ),
        )
        print()
        if ret != 0:
            print(f"❌ 上传失败: {local_path}")
            return ret

    print(f"\n✅ 上传完成: {cloud_dir}")
    return 0


def cmd_upload_ideas(
    project: str,
    cloud_base: str = "/BasicOFR/ideas",
    dry_run: bool = False,
    all_projects: bool = False,
) -> int:
    """上传 Ideas 大文件到云盘"""
    repo = _repo_root()
    ideas_root = repo / "Ideas"
    if not ideas_root.is_dir():
        print(f"错误: Ideas 目录不存在: {ideas_root}")
        return 1

    if all_projects:
        projects = sorted([p.name for p in ideas_root.iterdir() if p.is_dir()])
        if not projects:
            print(f"错误: Ideas 下未找到任何项目目录: {ideas_root}")
            return 1
    else:
        if not project:
            print("错误: 需要指定 --project 或 --all")
            return 1
        projects = [project]

    cloud_base = cloud_base.rstrip("/")
    exclude_args = _build_exclude_args(DEFAULT_EXCLUDES)

    def _upload_dir(local_dir: Path, remote_parent: str) -> int:
        cmd = ["aliyunpan", "upload"] + exclude_args + [str(local_dir), remote_parent]
        ret = _run_cmd_with_progress(
            cmd,
            dry_run,
            progress_callback=lambda p: print(
                f"\r⏫ {local_dir.name}: {p}", end="", flush=True
            ),
        )
        print()
        return ret

    def _upload_dir_contents(local_dir: Path, remote_dir: str) -> int:
        for child in sorted(local_dir.iterdir(), key=lambda x: x.name):
            cmd = ["aliyunpan", "upload"] + exclude_args + [str(child), remote_dir]
            ret = _run_cmd_with_progress(
                cmd,
                dry_run,
                progress_callback=lambda p, name=child.name: print(
                    f"\r⏫ {name}: {p}", end="", flush=True
                ),
            )
            print()
            if ret != 0:
                return ret
        return 0

    for proj in projects:
        proj_dir = ideas_root / proj
        if not proj_dir.is_dir():
            print(f"❌ 跳过：Ideas 项目目录不存在: {proj_dir}")
            if not all_projects:
                return 1
            continue

        remote_parent = f"{cloud_base}/{proj}"
        local_latex = proj_dir / "Latex"
        local_paper = proj_dir / "Paper"
        local_codes = proj_dir / "Codes"

        print(f"\n📤 上传 Ideas: {proj}")
        print(f"   目标云盘: {remote_parent}")
        print()

        uploaded_any = False

        # Latex（或兼容旧结构 Paper/ -> Latex/）
        if local_latex.is_dir():
            uploaded_any = True
            ret = _upload_dir(local_latex, remote_parent)
            if ret != 0:
                print(f"❌ 上传失败: {local_latex}")
                return ret
        elif local_paper.is_dir():
            uploaded_any = True
            ret = _upload_dir_contents(local_paper, f"{remote_parent}/Latex")
            if ret != 0:
                print(f"❌ 上传失败: {local_paper} -> {remote_parent}/Latex")
                return ret
        else:
            print("⚠️  未找到 Latex/ 或 Paper/，将跳过 LaTeX 上传")

        # Codes
        if local_codes.is_dir():
            uploaded_any = True
            ret = _upload_dir(local_codes, remote_parent)
            if ret != 0:
                print(f"❌ 上传失败: {local_codes}")
                return ret
        else:
            print("⚠️  未找到 Codes/，将跳过代码上传")

        if not uploaded_any:
            print("❌ 未上传任何内容（目录缺失）")
            if not all_projects:
                return 1

    print("\n✅ upload-ideas 完成")
    return 0


def cmd_upload_papers(
    paper_id: str,
    cloud_base: str = "/BasicOFR/papers",
    dry_run: bool = False,
) -> int:
    """上传论文图像到云盘"""
    repo = _repo_root()
    local_figures = repo / "Papers" / paper_id / "figures"
    if not local_figures.is_dir():
        print(f"错误: figures 目录不存在: {local_figures}")
        print("  期望结构: Papers/{paper_id}/figures/")
        return 1

    cloud_base = cloud_base.rstrip("/")
    remote_parent = f"{cloud_base}/{paper_id}"
    exclude_args = _build_exclude_args(DEFAULT_EXCLUDES)

    print(f"\n📤 上传 Papers: {paper_id}")
    print(f"   本地目录: {local_figures}")
    print(f"   目标云盘: {remote_parent}/figures")
    print()

    cmd = ["aliyunpan", "upload"] + exclude_args + [str(local_figures), remote_parent]
    ret = _run_cmd_with_progress(
        cmd,
        dry_run,
        progress_callback=lambda p: print(f"\r⏫ figures: {p}", end="", flush=True),
    )
    print()
    if ret != 0:
        print("❌ 上传失败")
        return ret

    print("\n✅ upload-papers 完成")
    return 0


def cmd_update_manifest(
    project: str,
    specs_file: Optional[str] = None,
) -> int:
    """更新 specs manifest 文件"""
    from manifest_gen import build_yaml_manifest

    repo = _repo_root()
    target = (
        Path(specs_file)
        if specs_file
        else repo / "specs" / project / "results" / "manifest.yaml"
    )
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        manifest = build_yaml_manifest(project=project)
    except Exception as e:
        print(f"错误: 生成 manifest 失败: {e}")
        return 1

    try:
        import yaml

        target.write_text(
            yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"错误: 写入 manifest 失败: {target} ({e})")
        return 1

    print(f"✅ 已更新: {target}")
    return 0


def cmd_download(
    cloud_path: str,
    save_to: Optional[str] = None,
    overwrite: bool = False,
    dry_run: bool = False,
) -> int:
    """从云盘下载文件"""
    repo = _repo_root()
    target = save_to or str(repo / "downloads")

    print(f"\n📥 下载云盘文件")
    print(f"   来源: {cloud_path}")
    print(f"   目标: {target}")
    print()

    cmd = ["aliyunpan", "download"]
    if overwrite:
        cmd.append("--ow")
    cmd.extend(["--saveto", target, cloud_path])

    ret = _run_cmd(cmd, dry_run)
    if ret == 0:
        print(f"\n✅ 下载完成: {target}")
    return ret


def cmd_sync(
    project: str,
    mode: str = "upload",
    cloud_base: str = "/BasicOFR",
    dry_run: bool = False,
) -> int:
    """同步本地与云盘"""
    if mode not in ("upload", "download"):
        print(f"错误: mode 必须是 'upload' 或 'download'，当前: {mode}")
        return 1

    repo = _repo_root()
    cloud_dir = f"{cloud_base}/{project}"

    print(f"\n🔄 同步实验目录: {project}")
    print(f"   模式: {mode}")
    print()

    for dirname in EXPERIMENT_DIRS:
        local_dir = repo / dirname / project
        if not local_dir.is_dir() and mode == "upload":
            continue

        cmd = [
            "aliyunpan", "sync", "start",
            "-ldir", str(local_dir),
            "-pdir", f"{cloud_dir}/{dirname}",
            "-mode", mode,
        ]

        print(f"📂 {dirname}/{project}")
        ret = _run_cmd(cmd, dry_run)
        if ret != 0:
            return ret

    print(f"\n✅ 同步完成")
    return 0


def cmd_list(cloud_path: str = "/BasicOFR", detailed: bool = False) -> int:
    """列出云盘文件"""
    cmd = ["aliyunpan", "ll" if detailed else "ls", cloud_path]
    return _run_cmd(cmd)


def cmd_check() -> int:
    """检查 aliyunpan 安装和登录状态"""
    print("🔍 检查 aliyunpan 状态...\n")

    # 检查安装
    result = subprocess.run(["which", "aliyunpan"], capture_output=True, text=True)
    if result.returncode != 0:
        print("❌ aliyunpan 未安装")
        print("\n安装方法:")
        print("  macOS: brew install tickstep/tap/aliyunpan")
        print("  Linux: 下载 https://github.com/tickstep/aliyunpan/releases")
        return 1

    print(f"✅ aliyunpan 已安装: {result.stdout.strip()}")

    # 检查登录
    result = subprocess.run(["aliyunpan", "who"], capture_output=True, text=True)
    if result.returncode != 0 or "未登录" in result.stdout:
        print("❌ 未登录阿里云盘")
        print("\n登录方法: aliyunpan login")
        return 1

    print(f"✅ 已登录: {result.stdout.strip()}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="aliyunpan 实验文件操作",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # upload-exp
    p_upload = subparsers.add_parser("upload-exp", help="上传实验产物")
    p_upload.add_argument("--project", "-p", required=True, help="项目名称")
    p_upload.add_argument("--timestamp", "-t", help="时间戳（默认：当前时间）")
    p_upload.add_argument("--cloud-base", default="/BasicOFR", help="云盘基础路径")
    p_upload.add_argument("--dry-run", action="store_true", help="仅显示命令，不执行")
    p_upload.add_argument("--exclude", "-e", action="append", help="额外排除规则")

    # upload-ideas
    p_ideas = subparsers.add_parser("upload-ideas", help="上传 Ideas 大文件")
    g = p_ideas.add_mutually_exclusive_group(required=True)
    g.add_argument("--project", "-p", help="Ideas 项目名称（如 WaveMamba）")
    g.add_argument("--all", action="store_true", help="上传 Ideas 下全部项目")
    p_ideas.add_argument("--cloud-base", default="/BasicOFR/ideas", help="云盘基础路径")
    p_ideas.add_argument("--dry-run", action="store_true", help="仅显示命令，不执行")

    # upload-papers
    p_papers = subparsers.add_parser("upload-papers", help="上传 Papers/{paper_id}/figures")
    p_papers.add_argument("--paper-id", required=True, help="论文 ID（对应 Papers/{paper_id}/）")
    p_papers.add_argument("--cloud-base", default="/BasicOFR/papers", help="云盘基础路径")
    p_papers.add_argument("--dry-run", action="store_true", help="仅显示命令，不执行")

    # update-manifest
    p_manifest = subparsers.add_parser("update-manifest", help="更新 specs manifest.yaml")
    p_manifest.add_argument("--project", "-p", required=True, help="specs 项目目录名（如 416-proj-dswinir）")
    p_manifest.add_argument(
        "--specs-file",
        help="指定输出 manifest.yaml 路径（默认：specs/<project>/results/manifest.yaml）",
    )

    # download-exp
    p_download = subparsers.add_parser("download-exp", help="下载云盘文件")
    p_download.add_argument("--cloud", "-c", required=True, help="云盘路径")
    p_download.add_argument("--saveto", "-s", help="保存目录")
    p_download.add_argument("--overwrite", "-o", action="store_true", help="覆盖已存在文件")
    p_download.add_argument("--dry-run", action="store_true", help="仅显示命令，不执行")

    # sync-exp
    p_sync = subparsers.add_parser("sync-exp", help="同步实验目录")
    p_sync.add_argument("--project", "-p", required=True, help="项目名称")
    p_sync.add_argument("--mode", "-m", default="upload", choices=["upload", "download"])
    p_sync.add_argument("--cloud-base", default="/BasicOFR", help="云盘基础路径")
    p_sync.add_argument("--dry-run", action="store_true", help="仅显示命令，不执行")

    # list-cloud
    p_list = subparsers.add_parser("list-cloud", help="列出云盘文件")
    p_list.add_argument("path", nargs="?", default="/BasicOFR", help="云盘路径")
    p_list.add_argument("--detailed", "-l", action="store_true", help="详细列表")

    # check
    subparsers.add_parser("check", help="检查 aliyunpan 状态")

    args = parser.parse_args()

    if args.command == "upload-exp":
        return cmd_upload(
            project=args.project,
            timestamp=args.timestamp,
            cloud_base=args.cloud_base,
            dry_run=args.dry_run,
            extra_excludes=args.exclude,
        )
    elif args.command == "upload-ideas":
        return cmd_upload_ideas(
            project=args.project or "",
            cloud_base=args.cloud_base,
            dry_run=args.dry_run,
            all_projects=args.all,
        )
    elif args.command == "upload-papers":
        return cmd_upload_papers(
            paper_id=args.paper_id,
            cloud_base=args.cloud_base,
            dry_run=args.dry_run,
        )
    elif args.command == "update-manifest":
        return cmd_update_manifest(
            project=args.project,
            specs_file=args.specs_file,
        )
    elif args.command == "download-exp":
        return cmd_download(
            cloud_path=args.cloud,
            save_to=args.saveto,
            overwrite=args.overwrite,
            dry_run=args.dry_run,
        )
    elif args.command == "sync-exp":
        return cmd_sync(
            project=args.project,
            mode=args.mode,
            cloud_base=args.cloud_base,
            dry_run=args.dry_run,
        )
    elif args.command == "list-cloud":
        return cmd_list(cloud_path=args.path, detailed=args.detailed)
    elif args.command == "check":
        return cmd_check()
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
