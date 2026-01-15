#!/usr/bin/env python3
"""
aliyunpan 上传清单 (manifest) 生成器

基于 .claude/scripts/aliyunpan_manifest.py 迁移，适配技能接口。

功能：
- 统计本地实验目录（文件数、字节数）
- 计算关键文件 SHA256 哈希
- 生成 JSON manifest 供追溯
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml


EXPERIMENT_DIRS = ["experiments", "tb_logger", "results"]

KEY_FILE_NAMES = {
    "train.log",
    "summary.md",
    "options.yml",
    "results.json",
    "metrics.json",
    "validation.log",
    "config.yml",
    "config.yaml",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def _walk_stats(root: Path) -> Tuple[int, int, List[Path]]:
    """统计目录：(file_count, total_bytes, key_files)"""
    file_count = 0
    total_bytes = 0
    key_files: List[Path] = []

    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            p = Path(dirpath) / fn
            try:
                st = p.stat()
            except OSError:
                continue
            file_count += 1
            total_bytes += int(st.st_size)
            if fn in KEY_FILE_NAMES:
                key_files.append(p)

    return file_count, total_bytes, key_files


def _pick_hash_files(
    paths: List[Path], max_files: int = 20, max_mb: int = 200
) -> List[Path]:
    """选择需要计算哈希的文件"""
    picked: List[Path] = []
    for p in sorted(paths):
        if not p.is_file():
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size > max_mb * 1024 * 1024:
            continue
        picked.append(p)
        if len(picked) >= max_files:
            break
    return picked


def generate_manifest(
    project: str,
    timestamp: Optional[str] = None,
    cloud_dir: Optional[str] = None,
    output_dir: Optional[Path] = None,
    max_hash_files: int = 20,
    max_hash_mb: int = 200,
) -> Dict[str, Any]:
    """生成上传清单"""
    repo = _repo_root()
    ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    cloud = cloud_dir or f"/BasicOFR/{project}/{ts}"

    items: List[Dict[str, Any]] = []

    for dirname in EXPERIMENT_DIRS:
        rel_path = f"{dirname}/{project}"
        abs_path = repo / dirname / project

        entry: Dict[str, Any] = {
            "path": rel_path,
            "abs_path": str(abs_path),
            "exists": abs_path.is_dir(),
        }

        if abs_path.is_dir():
            file_count, total_bytes, key_candidates = _walk_stats(abs_path)
            hash_files = _pick_hash_files(key_candidates, max_hash_files, max_hash_mb)

            hashes: Dict[str, str] = {}
            for hf in hash_files:
                try:
                    rel = str(hf.relative_to(repo))
                    hashes[rel] = _sha256(hf)
                except Exception:
                    continue

            entry.update({
                "file_count": file_count,
                "total_bytes": total_bytes,
                "total_mb": round(total_bytes / (1024 * 1024), 2),
                "key_hashes": hashes,
            })

        items.append(entry)

    manifest = {
        "schema": 2,
        "project": project,
        "timestamp": ts,
        "cloud_dir": cloud,
        "upload_policy": "B",
        "includes": [f"{d}/<project>" for d in EXPERIMENT_DIRS],
        "items": items,
        "generated_at": datetime.now().isoformat(),
    }

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / f"{ts}_manifest.json"
        out_file.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest["manifest_path"] = str(out_file)

    return manifest


def _project_slug(project: str) -> str:
    m = re.match(r"^\d+-proj-(?P<slug>.+)$", project)
    return m.group("slug") if m else project


def _resolve_project_dir(base: Path, candidates: List[str]) -> Optional[Path]:
    for name in candidates:
        p = base / name
        if p.is_dir():
            return p
    if not base.is_dir():
        return None
    lower_map = {p.name.lower(): p for p in base.iterdir() if p.is_dir()}
    for name in candidates:
        p = lower_map.get(name.lower())
        if p:
            return p
    return None


def _scan_visualizations(project: str, repo: Path) -> List[Dict[str, Any]]:
    results_base = repo / "results"
    candidates = [project, _project_slug(project)]
    project_dir = _resolve_project_dir(results_base, candidates)
    if not project_dir:
        return []

    results_dir = project_dir / "visualization"
    files: List[Dict[str, Any]] = []
    if not results_dir.exists():
        return files
    for ext in ("*.png", "*.jpg", "*.jpeg", "*.gif"):
        for f in results_dir.glob(ext):
            files.append({
                "local_path": str(f.relative_to(repo)),
                "type": "visualization",
            })
    return files


def _scan_checkpoints(project: str, repo: Path) -> List[Dict[str, Any]]:
    exp_base = repo / "experiments"
    candidates = [project, _project_slug(project)]
    project_dir = _resolve_project_dir(exp_base, candidates)
    if not project_dir:
        return []

    models_dir = project_dir / "models"
    files: List[Dict[str, Any]] = []
    if not models_dir.exists():
        return files
    for f in models_dir.glob("*.pth"):
        files.append({
            "local_path": str(f.relative_to(repo)),
            "type": "checkpoint",
        })
    return files


def _load_artifacts(project: str, repo: Path) -> List[Dict[str, Any]]:
    artifacts_path = repo / "specs" / project / "results" / "artifacts.yaml"
    if not artifacts_path.exists():
        return []
    try:
        data = yaml.safe_load(artifacts_path.read_text(encoding="utf-8")) or {}
        uploaded = data.get("uploaded", [])
        return [
            {
                "local_path": item.get("path", ""),
                "cloud_url": item.get("url", ""),
                "type": "uploaded",
            }
            for item in uploaded
            if item.get("path")
        ]
    except Exception:
        return []


def build_yaml_manifest(
    project: str,
    cloud_base: str = "aliyunpan://BasicOFR",
) -> Dict[str, Any]:
    """构造 specs/{project}/results/manifest.yaml 的内容（与 ofr-exp-test 格式兼容）"""
    repo = _repo_root()

    cloud_base = cloud_base.rstrip("/")
    exp_root = cloud_base if cloud_base.endswith("/experiments") else f"{cloud_base}/experiments"
    remote_project = _project_slug(project)

    results_cloud = f"{exp_root}/{remote_project}/results/"
    checkpoints_cloud = f"{exp_root}/{remote_project}/checkpoints/"

    files: List[Dict[str, Any]] = []

    # 1) 复用 artifacts.yaml（若存在）
    files.extend(_load_artifacts(project, repo))

    # 2) results/<project>/visualization/*.png|jpg|...
    for item in _scan_visualizations(project, repo):
        filename = Path(item["local_path"]).name
        item["cloud_url"] = f"{results_cloud}{filename}"
        files.append(item)

    # 3) experiments/<project>/models/*.pth
    for item in _scan_checkpoints(project, repo):
        filename = Path(item["local_path"]).name
        item["cloud_url"] = f"{checkpoints_cloud}{filename}"
        files.append(item)

    return {
        "project": project,
        "cloud_base": results_cloud,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 aliyunpan 上传清单")
    parser.add_argument("--project", "-p", required=True, help="项目名称")
    parser.add_argument("--timestamp", "-t", help="时间戳（默认：当前时间）")
    parser.add_argument("--cloud-dir", "-c", help="云盘目标目录")
    parser.add_argument("--output", "-o", type=Path, help="输出目录")
    parser.add_argument("--max-hash-files", type=int, default=20)
    parser.add_argument("--max-hash-mb", type=int, default=200)
    parser.add_argument("--pretty", action="store_true", help="格式化 JSON 输出")

    args = parser.parse_args()

    manifest = generate_manifest(
        project=args.project,
        timestamp=args.timestamp,
        cloud_dir=args.cloud_dir,
        output_dir=args.output,
        max_hash_files=args.max_hash_files,
        max_hash_mb=args.max_hash_mb,
    )

    print(json.dumps(manifest, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())


def generate_yaml_manifest(
    project: str,
    cloud_base: str = "aliyunpan://BasicOFR",
) -> Path:
    """生成 specs/{project}/results/manifest.yaml（与 ofr-exp-test/scripts/manifest_generator.py 格式兼容）"""
    repo = _repo_root()
    spec_results_dir = repo / "specs" / project / "results"
    spec_results_dir.mkdir(parents=True, exist_ok=True)

    manifest = build_yaml_manifest(project=project, cloud_base=cloud_base)
    out_path = spec_results_dir / "manifest.yaml"
    out_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return out_path
