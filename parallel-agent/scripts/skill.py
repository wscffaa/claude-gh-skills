#!/usr/bin/env python3
"""Parallel agent skill entrypoint."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import List

from dag_scheduler import DAGSchedulerError, topological_layers
from executor import DEFAULT_TIMEOUT, TaskResult, execute_layers
from task_parser import TaskParseError, TaskSpec, parse_tasks


def log_error(message: str) -> None:
    sys.stderr.write(f"ERROR: {message}\n")


def log_info(message: str) -> None:
    sys.stderr.write(f"INFO: {message}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="parallel-agent skill (async DAG executor)",
        add_help=True,
    )
    parser.add_argument(
        "--backend",
        default=os.environ.get("CODEAGENT_BACKEND", "codex"),
        help="default backend for tasks without backend field",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="timeout in seconds for each task",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help="启用进度报告（LLM 输出 [PROGRESS] 标记）",
    )
    args = parser.parse_args()

    env_timeout = os.environ.get("CODEX_TIMEOUT")
    if env_timeout:
        try:
            args.timeout = int(env_timeout)
        except ValueError:
            log_error(f"Invalid CODEX_TIMEOUT value: {env_timeout!r}")

    if not args.backend or not args.backend.strip():
        args.backend = "codex"

    return args


def format_summary(results: List[TaskResult]) -> str:
    success = 0
    failed = 0
    for result in results:
        if result.exit_code == 0 and not result.error:
            success += 1
        else:
            failed += 1

    lines: List[str] = []
    lines.append("=== Parallel Execution Summary ===")
    lines.append(f"Total: {len(results)} | Success: {success} | Failed: {failed}")
    lines.append("")

    for result in results:
        lines.append(f"--- Task: {result.task_id} ---")
        if result.skipped:
            lines.append("Status: SKIPPED")
            if result.error:
                lines.append(f"Reason: {result.error}")
        elif result.error:
            lines.append(f"Status: FAILED (exit code {result.exit_code})")
            lines.append(f"Error: {result.error}")
        elif result.exit_code != 0:
            lines.append(f"Status: FAILED (exit code {result.exit_code})")
        else:
            lines.append("Status: SUCCESS")

        if result.session_id:
            lines.append(f"Session: {result.session_id}")
        if result.message:
            lines.append("")
            lines.append(result.message)
        lines.append("")

    return "\n".join(lines)


def apply_default_backend(tasks: List[TaskSpec], default_backend: str) -> None:
    for task in tasks:
        if not task.backend.strip():
            task.backend = default_backend


def main() -> int:
    args = parse_args()
    raw_input = sys.stdin.read()

    try:
        tasks = parse_tasks(raw_input)
    except TaskParseError as exc:
        log_error(str(exc))
        return 1

    apply_default_backend(tasks, args.backend)

    try:
        layers = topological_layers(tasks)
    except DAGSchedulerError as exc:
        log_error(str(exc))
        return 1

    log_info(f"Loaded {len(tasks)} tasks in {len(layers)} layers")

    results = asyncio.run(execute_layers(layers, args.timeout, args.backend, enable_progress=args.progress))
    print(format_summary(results))

    exit_code = 0
    for result in results:
        if result.exit_code != 0:
            exit_code = result.exit_code
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
