#!/usr/bin/env python3
"""Async task execution for parallel-agent skill."""
from __future__ import annotations

import asyncio
import os
import re
import sys
from dataclasses import dataclass
from typing import Dict, List, Tuple

from compressor import compress_text
from json_parsers import parse_stream_json
from task_parser import TaskSpec

DEFAULT_TIMEOUT = 7200
FORCE_KILL_DELAY = 5
PROGRESS_INSTRUCTION = """

---
【进度报告要求】
每完成一个关键步骤后，输出一行进度信息，格式：
[PROGRESS] <简短描述当前完成的操作，15字以内>

示例：
[PROGRESS] 读取 network.py 分析结构
[PROGRESS] 创建 wavelet_block.py (87行)
[PROGRESS] 修改 __init__.py 添加导出
[PROGRESS] 运行测试，全部通过
"""

READ_MORE_PATTERN = re.compile(r'\[READ_MORE\]\s+section="([^"]+)"')


def extract_read_more_requests(text: str) -> List[str]:
    """提取 [READ_MORE] 请求的章节列表（最多2个）"""
    matches = READ_MORE_PATTERN.findall(text)
    return matches[:2]  # 限制最多2个


def read_paper_section(paper_path: str, section_title: str) -> str:
    """调用 paper_extractor.py 读取章节"""
    import subprocess
    result = subprocess.run(
        ["python3", ".claude/scripts/paper_extractor.py", "--input", paper_path, "--read-section", section_title],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return f"[ERROR] 读取章节失败: {result.stderr}"
    return result.stdout


@dataclass
class TaskResult:
    task_id: str
    exit_code: int
    message: str = ""
    session_id: str = ""
    error: str = ""
    skipped: bool = False


def log_info(message: str) -> None:
    sys.stderr.write(f"INFO: {message}\n")


def log_warn(message: str) -> None:
    sys.stderr.write(f"WARN: {message}\n")


def log_error(message: str) -> None:
    sys.stderr.write(f"ERROR: {message}\n")


def should_skip(task: TaskSpec, failed: Dict[str, TaskResult]) -> Tuple[bool, str]:
    if not task.dependencies:
        return False, ""

    blocked = [dep for dep in task.dependencies if dep in failed]
    if not blocked:
        return False, ""

    return True, f"skipped due to failed dependencies: {', '.join(blocked)}"


def _resolve_backend(task: TaskSpec, default_backend: str) -> str:
    backend = (task.backend or default_backend or "").strip().lower()
    return backend


def build_dependency_context(task: TaskSpec, outputs: Dict[str, str]) -> str:
    """Build compressed dependency context for a task.

    Args:
        task: The task that needs dependency context
        outputs: Dict of task_id -> output message from completed tasks

    Returns:
        Compressed dependency context string, or empty string if not applicable
    """
    if not task.compress or not task.dependencies:
        return ""

    compressed_parts: List[str] = []
    for dep in task.dependencies:
        dep_output = outputs.get(dep, "")
        if not dep_output.strip():
            continue
        summary = compress_text(dep_output, ratio=task.compress_ratio, model=task.compress_model)
        compressed_parts.append(f"### {dep}\n{summary}")

    if not compressed_parts:
        return ""

    header = (
        f"[依赖任务压缩输出 | 模型: {task.compress_model}, 压缩比: {task.compress_ratio:.0%}]\n"
        "如需更详细信息，可请求展开对应任务。"
    )
    return f"\n\n---\n{header}\n\n" + "\n\n".join(compressed_parts) + "\n\n---\n"


def build_command(task: TaskSpec, default_backend: str, enable_progress: bool = False) -> Tuple[List[str], str, str, dict]:
    """Build command for task execution.

    Returns:
        Tuple of (args, workdir, modified_content, env_vars)
        For backends that don't support native image params, images are embedded in content.
        env_vars contains additional environment variables to set for the process.
    """
    backend = _resolve_backend(task, default_backend)
    workdir = task.workdir or "."
    model = (task.model or "").strip()
    reasoning_effort = (task.reasoning_effort or "").strip()
    content = task.content
    images = task.images or []
    env_vars = {}

    if backend == "codex":
        if task.mode == "resume":
            if not task.session_id.strip():
                raise ValueError("resume mode requires non-empty session_id")
            args = ["codex", "e", "--json", "--skip-git-repo-check",
                    "--dangerously-bypass-approvals-and-sandbox"]
            if model:
                args += ["-m", model]
            # Add image parameters for Codex
            for img in images:
                args += ["-i", img]
            args += ["resume", task.session_id, "-"]
        else:
            args = ["codex", "e", "-C", workdir,
                    "--dangerously-bypass-approvals-and-sandbox"]
            if model:
                args += ["-m", model]
            # Add image parameters for Codex
            for img in images:
                args += ["-i", img]
            args += ["--json", "--skip-git-repo-check", "-"]
        # Set reasoning_effort via environment variable if specified
        # Codex CLI reads CODEX_MODEL_REASONING_EFFORT from environment
        if reasoning_effort:
            env_vars["CODEX_MODEL_REASONING_EFFORT"] = reasoning_effort
        if enable_progress:
            content = f"{content}{PROGRESS_INSTRUCTION}"
        return args, workdir, content, env_vars

    if backend == "claude":
        args = ["claude", "-p", "--verbose", "--setting-sources", "", "--output-format", "stream-json"]
        if model:
            args += ["--model", model]
        if task.mode == "resume":
            if not task.session_id.strip():
                raise ValueError("resume mode requires non-empty session_id")
            args += ["-r", task.session_id]
        args.append("-")
        # For Claude, embed images in content as Read tool instructions
        if images:
            from pathlib import Path
            image_instructions = "\n".join(
                f"请使用 Read 工具读取图片: {Path(img).resolve()}" for img in images
            )
            content = f"{image_instructions}\n\n{content}"
        if enable_progress:
            content = f"{content}{PROGRESS_INSTRUCTION}"
        return args, workdir, content, env_vars

    if backend == "gemini":
        args = ["gemini", "-o", "stream-json", "-y"]
        if model:
            args += ["-m", model]
        if task.mode == "resume":
            if not task.session_id.strip():
                raise ValueError("resume mode requires non-empty session_id")
            args += ["-r", task.session_id]
        args += ["-p", "-"]
        # For Gemini, embed images in content using @filepath syntax
        if images:
            from pathlib import Path
            image_refs = " ".join(f"@{Path(img).resolve()}" for img in images)
            content = f"分析以下图片: {image_refs}\n\n{content}"
        if enable_progress:
            content = f"{content}{PROGRESS_INSTRUCTION}"
        return args, workdir, content, env_vars

    raise ValueError(f"unsupported backend {backend!r}")


async def run_task(task: TaskSpec, timeout: int, default_backend: str, enable_progress: bool = False) -> TaskResult:
    backend = _resolve_backend(task, default_backend)
    prefix = f"[Task {task.task_id}]"

    if task.workdir and not os.path.isdir(task.workdir):
        return TaskResult(task_id=task.task_id, exit_code=1, error=f"workdir not found: {task.workdir}")

    # Validate image files exist
    for img in (task.images or []):
        if not os.path.isfile(img):
            return TaskResult(task_id=task.task_id, exit_code=1, error=f"image file not found: {img}")

    try:
        args, workdir, content, env_vars = build_command(task, default_backend, enable_progress=enable_progress)
    except ValueError as exc:
        return TaskResult(task_id=task.task_id, exit_code=1, error=str(exc))

    log_info(f"{prefix} start backend={backend} workdir={workdir}")
    if task.images:
        log_info(f"{prefix} images: {task.images}")
    if task.reasoning_effort:
        log_info(f"{prefix} reasoning_effort: {task.reasoning_effort}")
    log_info(f"{prefix} command: {' '.join(args)}")

    # Merge environment variables with current environment
    process_env = os.environ.copy()
    process_env.update(env_vars)

    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir or None,
            env=process_env,
        )

        async def _stream_output() -> Tuple[str, bytes]:
            if process.stdin is None:
                raise RuntimeError("stdin is not available")

            process.stdin.write(content.encode("utf-8"))
            await process.stdin.drain()
            process.stdin.close()

            stdout_lines: List[str] = []
            if process.stdout is not None:
                async for line in process.stdout:
                    line_str = line.decode("utf-8", errors="replace")
                    stdout_lines.append(line_str)
                    if enable_progress and "[PROGRESS]" in line_str:
                        progress_match = re.search(r"\[PROGRESS\]\s*(.+)", line_str)
                        if progress_match:
                            log_info(f"[PROGRESS] {task.task_id} | {progress_match.group(1).strip()}")

            stderr_data = b""
            if process.stderr is not None:
                stderr_data = await process.stderr.read()

            await process.wait()
            return "".join(stdout_lines), stderr_data

        stdout_text, stderr_bytes = await asyncio.wait_for(_stream_output(), timeout=timeout)
    except asyncio.TimeoutError:
        if process is not None:
            process.kill()
            try:
                await asyncio.wait_for(process.wait(), timeout=FORCE_KILL_DELAY)
            except asyncio.TimeoutError:
                pass
        return TaskResult(task_id=task.task_id, exit_code=124, error="execution timeout")
    except FileNotFoundError:
        return TaskResult(task_id=task.task_id, exit_code=127, error=f"backend command not found: {backend}")
    except Exception as exc:
        return TaskResult(task_id=task.task_id, exit_code=1, error=f"execution failed: {exc}")

    stderr_text = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

    parse_result = parse_stream_json(backend, stdout_text)
    message = parse_result.message
    session_id = parse_result.session_id or task.session_id

    # 检测 [READ_MORE] 标记
    if process.returncode == 0 and message and "[READ_MORE]" in message:
        paper_path = task.metadata.get("paper_path") if getattr(task, "metadata", None) else None
        if paper_path:
            if task.metadata is None:
                task.metadata = {}
            try:
                read_more_attempts = int(task.metadata.get("_read_more_attempts", "0"))
            except (TypeError, ValueError):
                read_more_attempts = 0

            if read_more_attempts < 1:
                sections = extract_read_more_requests(message)
                if sections:
                    log_info(f"{prefix} detected READ_MORE requests: {sections}")

                    additional_content: List[str] = []
                    for section in sections:
                        content = read_paper_section(paper_path, section)
                        additional_content.append(f"\n---\n## [已读取] {section}\n\n{content}")

                    task.metadata["_read_more_attempts"] = str(read_more_attempts + 1)
                    task.content += "".join(additional_content) + "\n\n请继续完成分析。"
                    log_info(f"{prefix} re-running with additional sections")
                    return await run_task(task, timeout, default_backend, enable_progress)
            else:
                log_warn(f"{prefix} READ_MORE ignored to prevent recursion")
        else:
            log_warn(f"{prefix} READ_MORE requested but no paper_path provided")

    exit_code = process.returncode if process.returncode is not None else 1
    error = ""
    if exit_code != 0:
        error = stderr_text.strip() or f"backend exited with status {exit_code}"
        if error:
            log_warn(f"{prefix} {error}")

    log_info(f"{prefix} complete exit={exit_code}")
    return TaskResult(
        task_id=task.task_id,
        exit_code=exit_code,
        message=message,
        session_id=session_id,
        error=error,
    )


async def execute_layers(
    layers: List[List[TaskSpec]], timeout: int, default_backend: str, enable_progress: bool = False
) -> List[TaskResult]:
    results: List[TaskResult] = []
    failed: Dict[str, TaskResult] = {}
    outputs: Dict[str, str] = {}  # Store task outputs for compression

    for layer in layers:
        pending: List[TaskSpec] = []
        for task in layer:
            skip, reason = should_skip(task, failed)
            if skip:
                result = TaskResult(
                    task_id=task.task_id,
                    exit_code=1,
                    error=reason,
                    skipped=True,
                )
                results.append(result)
                failed[task.task_id] = result
            else:
                # Inject compressed dependency context if enabled
                if task.compress:
                    dep_context = build_dependency_context(task, outputs)
                    if dep_context:
                        task.content = f"{task.content}\n\n{dep_context}"
                        injected = [dep for dep in task.dependencies if dep in outputs]
                        if injected:
                            log_info(f"[Task {task.task_id}] injected compressed deps: {', '.join(injected)}")
                pending.append(task)

        if not pending:
            continue

        layer_results = await asyncio.gather(
            *(run_task(task, timeout, default_backend, enable_progress=enable_progress) for task in pending)
        )
        results.extend(layer_results)
        for result in layer_results:
            if result.exit_code != 0 or result.error:
                failed[result.task_id] = result
            else:
                # Store successful outputs for downstream compression
                outputs[result.task_id] = result.message

    return results
