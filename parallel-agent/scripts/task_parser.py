#!/usr/bin/env python3
"""Task parsing for parallel-agent skill."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

TASK_SEPARATOR = "---TASK---"
CONTENT_SEPARATOR = "---CONTENT---"
DEFAULT_WORKDIR = "."


@dataclass
class TaskSpec:
    task_id: str
    content: str
    workdir: str = DEFAULT_WORKDIR
    backend: str = ""
    model: str = ""
    reasoning_effort: str = ""  # For Codex: minimal, low, medium, high
    dependencies: List[str] = field(default_factory=list)
    images: List[str] = field(default_factory=list)
    session_id: str = ""
    mode: str = "new"
    compress: bool = False  # 是否压缩依赖输出
    compress_model: str = "flash"  # 压缩模型：flash/pro
    compress_ratio: float = 0.3  # 目标压缩比
    metadata: Optional[Dict[str, str]] = None


class TaskParseError(ValueError):
    pass


def _normalize_dependencies(value: str) -> List[str]:
    deps: List[str] = []
    for dep in value.split(","):
        dep = dep.strip()
        if dep:
            deps.append(dep)
    return deps


def _normalize_images(value: str) -> List[str]:
    """Parse comma-separated image paths."""
    images: List[str] = []
    for img in value.split(","):
        img = img.strip()
        if img:
            images.append(img)
    return images


def _parse_bool_flag(value: str) -> bool:
    """Parse boolean flag from string."""
    value_norm = value.strip().lower()
    return value_norm in {"1", "true", "yes", "y", "on"}


def _parse_compress_ratio(value: str) -> float:
    """Parse compression ratio, must be between 0 and 1."""
    try:
        ratio = float(value)
    except ValueError:
        raise TaskParseError(f"invalid compress_ratio: {value!r}")
    if ratio <= 0 or ratio > 1:
        raise TaskParseError("compress_ratio must be between 0 and 1")
    return ratio


def parse_tasks(data: str, default_workdir: str = DEFAULT_WORKDIR) -> List[TaskSpec]:
    trimmed = data.strip()
    if not trimmed:
        raise TaskParseError("parallel config is empty")

    tasks: List[TaskSpec] = []
    seen = set()

    blocks = trimmed.split(TASK_SEPARATOR)
    task_index = 0
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        task_index += 1

        parts = block.split(CONTENT_SEPARATOR, 1)
        if len(parts) != 2:
            raise TaskParseError(f"task block #{task_index} missing ---CONTENT--- separator")

        meta = parts[0].strip()
        content = parts[1].strip()

        task_id = ""
        workdir = default_workdir
        backend = ""
        model = ""
        reasoning_effort = ""
        dependencies: List[str] = []
        images: List[str] = []
        session_id = ""
        mode = "new"
        compress = False
        compress_model = "flash"
        compress_ratio = 0.3

        for line in meta.splitlines():
            line = line.strip()
            if not line:
                continue
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if key == "id":
                task_id = value
            elif key == "workdir":
                workdir = value
            elif key == "session_id":
                session_id = value
                mode = "resume"
            elif key == "backend":
                backend = value
            elif key == "model":
                model = value
            elif key == "reasoning_effort":
                reasoning_effort = value
            elif key == "dependencies":
                dependencies.extend(_normalize_dependencies(value))
            elif key == "images":
                images.extend(_normalize_images(value))
            elif key == "compress":
                compress = _parse_bool_flag(value)
            elif key == "compress_model":
                compress_model = value or "flash"
            elif key == "compress_ratio":
                compress_ratio = _parse_compress_ratio(value)

        if not task_id:
            raise TaskParseError(f"task block #{task_index} missing id field")
        if not content:
            raise TaskParseError(f"task block #{task_index} ({task_id!r}) missing content")
        if mode == "resume" and not session_id.strip():
            raise TaskParseError(f"task block #{task_index} ({task_id!r}) has empty session_id")
        if task_id in seen:
            raise TaskParseError(f"task block #{task_index} has duplicate id: {task_id}")

        tasks.append(
            TaskSpec(
                task_id=task_id,
                content=content,
                workdir=workdir,
                backend=backend,
                model=model,
                reasoning_effort=reasoning_effort,
                dependencies=dependencies,
                images=images,
                session_id=session_id,
                mode=mode,
                compress=compress,
                compress_model=compress_model,
                compress_ratio=compress_ratio,
            )
        )
        seen.add(task_id)

    if not tasks:
        raise TaskParseError("no tasks found")

    return tasks
