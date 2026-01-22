#!/usr/bin/env python3
"""
根据 priority_batcher.py --json 的输出，按批次并发执行 issue。

功能:
- 从 stdin 或 --input 文件读取 JSON（priority_batcher.py --json 输出）
- 每批次内支持并发执行（DAG 调度，依赖感知）
- 自适应并发数：根据优先级和依赖关系动态调整
  - P0: max_workers=4（紧急，高并发）
  - P1: max_workers=3
  - P2: max_workers=2
  - P3: max_workers=1
  - 有依赖时并发数 -1
- 复用 worktree.py 脚本进行 worktree 管理（同目录下的 worktree.py）
- 每个 issue 创建独立 worktree: {repo}-worktrees/issue-{number}
- 使用 codeagent-wrapper 执行单个 issue（stdin 传入任务说明）
- 失败支持重试：清理 worktree 与远程分支后重试（--max-retries）
- 若检测到对应 PR（head=issue-{number}），自动执行 PR Review（codeagent-wrapper --backend codex）并合并（gh pr merge --squash --delete-branch）
- issue 完成后自动清理 worktree
- Ctrl+C（SIGINT）时清理当前 worktree 并输出已完成报告

输出格式:
- 开始处理: 🚀 开始处理 (共 {total} 个 issues)
- 每个批次开始: 📦 {PRIORITY} 批次 ({count} issues, 并发={workers})
- 每个 issue 开始: [2/10] 正在处理 Issue #42: xxx (P1)
- 每个 issue 完成: ✅ Issue #42 已完成，PR #123 已合并 (耗时 2m30s)
- 每个 issue 失败: ❌ Issue #42 失败 (尝试 2/4): xxx
- 每个批次完成: 📦 {PRIORITY} 批次完成 ({completed}/{total})
- 最终输出完成报告
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shlex
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, Optional, TextIO

# 导入安全命令构造模块（如果存在）
try:
    from safe_command import (
        SafeCommandBuilder,
        run_command_with_stdin,
        build_codeagent_command,
        escape_for_logging,
    )
    HAS_SAFE_COMMAND = True
except ImportError:
    HAS_SAFE_COMMAND = False


DEFAULT_WORKTREE_SCRIPT = Path(__file__).parent / "worktree.py"
SESSION_ID_PATTERN = re.compile(r"\bSESSION_ID\s*[:=]\s*([A-Za-z0-9._-]+)")
ISSUE_BRANCH_PATTERN = re.compile(r"\bissue-(\d+)\b")


@dataclass
class IssueSpec:
    number: int
    priority: str
    title: str = ""
    dependencies: list[int] = field(default_factory=list)


@dataclass
class IssueResult:
    number: int
    priority: str
    title: str
    status: str  # completed | failed | interrupted | skipped
    pr_number: Optional[int] = None  # PR 编号
    elapsed_sec: float = 0.0  # 耗时（秒）
    attempts: int = 1
    returncode: Optional[int] = None
    detail: str = ""


@dataclass
class ExecState:
    interrupted: bool = False
    current_issue: Optional[int] = None
    current_worktree_path: Optional[Path] = None
    current_process: Optional[subprocess.Popen] = None
    last_process: Optional[subprocess.Popen] = None
    session_ids: dict[int, str] = field(default_factory=dict)
    # 资源追踪：用于 finally 阶段统一清理
    created_issues: set[int] = field(default_factory=set)
    # 并发执行相关
    active_issues: set[int] = field(default_factory=set)
    active_processes: dict[int, subprocess.Popen] = field(default_factory=dict)
    active_worktrees: dict[int, Path] = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock)


@dataclass
class CleanupReport:
    tracked_issues: list[int] = field(default_factory=list)
    worktree_removed: dict[int, tuple[bool, str]] = field(default_factory=dict)
    worktree_force_used: set[int] = field(default_factory=set)
    local_branch_deleted: dict[int, tuple[bool, str]] = field(default_factory=dict)
    remote_branch_deleted: dict[int, tuple[bool, str]] = field(default_factory=dict)
    prune_ok: bool = False
    prune_detail: str = ""


# ==================== 自适应并发数计算 ====================

def _calculate_max_workers(priority: str, batch_size: int, has_dependencies: bool) -> int:
    """
    根据优先级和依赖关系计算最大并发数。

    规则：
    - P0: 基础并发数 4（紧急任务，尽快完成）
    - P1: 基础并发数 3
    - P2: 基础并发数 2
    - P3: 基础并发数 1（低优先级，节省资源）
    - 有依赖时：并发数 -1（避免过多等待）
    - 最终取 min(base, batch_size)
    """
    base = {
        "p0": 4,
        "p1": 3,
        "p2": 2,
        "p3": 1,
    }.get(priority.lower(), 2)

    if has_dependencies:
        base = max(1, base - 1)

    return max(1, min(base, batch_size))


# ==================== DAG 调度器 ====================

class DagScheduler:
    """
    依赖感知的 DAG 调度器。

    - 维护 pending/in_progress/completed 三个状态集合
    - get_ready_issues() 返回所有依赖已完成的待处理 issue
    - 支持并发调用（线程安全）
    """

    def __init__(self, specs: list[IssueSpec]):
        self.specs = {s.number: s for s in specs}
        self.completed: set[int] = set()
        self.failed: set[int] = set()
        self.in_progress: set[int] = set()
        self.pending: set[int] = set(s.number for s in specs)
        self._lock = Lock()

    def get_ready_issues(self) -> list[int]:
        """返回所有依赖已完成且未开始的 issue 编号"""
        with self._lock:
            ready = []
            for num in list(self.pending):
                spec = self.specs[num]
                # 依赖必须全部完成（不含失败）
                deps_met = all(
                    dep in self.completed
                    for dep in spec.dependencies
                    if dep in self.specs
                )
                if deps_met:
                    ready.append(num)
            return ready

    def mark_started(self, num: int) -> bool:
        """标记 issue 为进行中，返回是否成功"""
        with self._lock:
            if num not in self.pending:
                return False
            self.pending.discard(num)
            self.in_progress.add(num)
            return True

    def mark_completed(self, num: int):
        """标记 issue 为已完成"""
        with self._lock:
            self.in_progress.discard(num)
            self.completed.add(num)

    def mark_failed(self, num: int):
        """标记 issue 为失败"""
        with self._lock:
            self.in_progress.discard(num)
            self.failed.add(num)

    def is_done(self) -> bool:
        """检查是否所有 issue 都已处理"""
        with self._lock:
            return len(self.pending) == 0 and len(self.in_progress) == 0

    def has_blocked_issues(self) -> list[int]:
        """返回因依赖失败而被阻塞的 issue"""
        with self._lock:
            blocked = []
            for num in list(self.pending):
                spec = self.specs[num]
                # 如果任一依赖失败，则该 issue 被阻塞
                if any(dep in self.failed for dep in spec.dependencies if dep in self.specs):
                    blocked.append(num)
            return blocked


def _read_json_input(path: Optional[str]) -> dict[str, Any]:
    if path and path != "-":
        try:
            raw = Path(path).read_text(encoding="utf-8")
        except OSError as e:
            print(f"Error: 读取输入文件失败: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        if sys.stdin.isatty():
            print("Error: 未提供输入，请通过 stdin 管道或 --input 指定 JSON 文件", file=sys.stderr)
            sys.exit(1)
        raw = sys.stdin.read()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Error: JSON 解析失败: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(data, dict):
        print("Error: 输入 JSON 顶层必须为对象（包含 batches 字段）", file=sys.stderr)
        sys.exit(1)
    return data


def _extract_specs(data: dict[str, Any]) -> tuple[list[IssueSpec], list[str]]:
    """
    从 priority_batcher.py --json 输出中提取 IssueSpec 列表。

    支持两种格式：
    1. 新格式（推荐）：issues 为对象数组
       {"number": 42, "title": "xxx", "dependencies": [41]}
    2. 旧格式（兼容）：issues 为整数数组
       [42, 43, 44]
    """
    batches = data.get("batches")
    if not isinstance(batches, list):
        print("Error: 输入 JSON 缺少 batches 列表（priority_batcher.py --json 输出）", file=sys.stderr)
        sys.exit(1)

    warnings: list[str] = []
    specs: list[IssueSpec] = []
    seen: set[int] = set()

    for batch in batches:
        if not isinstance(batch, dict):
            continue
        priority = str(batch.get("priority", "")).strip().lower()
        raw_issues = batch.get("issues")
        if not isinstance(raw_issues, list):
            continue
        for raw in raw_issues:
            number: Optional[int] = None
            title: str = ""
            dependencies: list[int] = []

            # 新格式：对象
            if isinstance(raw, dict):
                number = raw.get("number")
                if not isinstance(number, int):
                    continue
                title = str(raw.get("title", ""))
                raw_deps = raw.get("dependencies", [])
                if isinstance(raw_deps, list):
                    dependencies = [d for d in raw_deps if isinstance(d, int)]
            # 旧格式：整数
            elif isinstance(raw, int):
                number = raw
            elif isinstance(raw, str) and raw.strip().isdigit():
                number = int(raw.strip())

            if not number or number <= 0:
                continue
            if number in seen:
                warnings.append(f"重复 issue: #{number} 已跳过重复条目")
                continue
            seen.add(number)
            specs.append(IssueSpec(
                number=number,
                priority=priority or "p2",
                title=title,
                dependencies=dependencies,
            ))

    return specs, warnings


def _open_tty_stdin() -> Optional[TextIO]:
    if sys.stdin.isatty():
        return None
    try:
        return open("/dev/tty", "r")
    except OSError:
        return None


def _run_gh_issue_title(issue_number: int, repo: Optional[str], cwd: Path) -> str:
    cmd = ["gh", "issue", "view", str(issue_number)]
    if repo:
        cmd += ["--repo", repo]
    cmd += ["--json", "title", "-q", ".title"]

    for attempt in range(2):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=20, cwd=str(cwd))
        except subprocess.TimeoutExpired:
            if attempt == 0:
                continue
            return ""
        except FileNotFoundError:
            return ""
        except Exception:
            return ""

        if result.returncode == 0:
            return (result.stdout or "").strip()
        if attempt == 0:
            continue
        return ""

    return ""


def _last_nonempty_line(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def _parse_session_id(text: str) -> Optional[str]:
    if not text:
        return None
    matches = SESSION_ID_PATTERN.findall(text)
    if not matches:
        return None
    return matches[-1]


def _stop_process(proc: subprocess.Popen, timeout_sec: float = 5.0) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.send_signal(signal.SIGINT)
    except Exception:
        pass
    try:
        proc.wait(timeout=timeout_sec)
        return
    except subprocess.TimeoutExpired:
        pass

    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=timeout_sec)
        return
    except subprocess.TimeoutExpired:
        pass

    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=timeout_sec)
    except Exception:
        pass


def _run_capture(cmd: list[str], cwd: Path, state: ExecState) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError as e:
        return subprocess.CompletedProcess(cmd, 127, "", str(e))

    state.current_process = proc
    state.last_process = proc
    try:
        stdout, stderr = proc.communicate()
    finally:
        state.current_process = None

    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def _create_worktree(script_path: Path, issue_number: int, repo_dir: Path, state: ExecState) -> Path:
    result = _run_capture(["python3", str(script_path), "create", str(issue_number)], cwd=repo_dir, state=state)
    if result.returncode != 0:
        detail = (result.stderr or "").strip() or (result.stdout or "").strip()
        raise RuntimeError(detail or f"worktree create 失败（exit={result.returncode}）")

    path_str = _last_nonempty_line(result.stdout)
    if not path_str:
        probe = _run_capture(["python3", str(script_path), "path", str(issue_number)], cwd=repo_dir, state=state)
        if probe.returncode == 0:
            path_str = (probe.stdout or "").strip()

    if not path_str:
        raise RuntimeError("无法解析 worktree 路径")
    return Path(path_str)


def _remove_worktree(script_path: Path, issue_number: int, repo_dir: Path, state: ExecState) -> tuple[bool, str]:
    result = _run_capture(["python3", str(script_path), "remove", str(issue_number)], cwd=repo_dir, state=state)
    if result.returncode == 0:
        return True, ""
    detail = (result.stderr or "").strip() or (result.stdout or "").strip()
    return False, detail or f"worktree remove 失败（exit={result.returncode}）"


def _get_worktree_path(script_path: Path, issue_number: int, repo_dir: Path, state: ExecState) -> Optional[Path]:
    result = _run_capture(["python3", str(script_path), "path", str(issue_number)], cwd=repo_dir, state=state)
    if result.returncode != 0:
        return None
    path_str = (result.stdout or "").strip()
    return Path(path_str) if path_str else None


def _force_remove_worktree(issue_number: int, worktree_path: Path, repo_dir: Path, state: ExecState) -> tuple[bool, str]:
    result = _run_capture(
        ["git", "worktree", "remove", "--force", str(worktree_path)],
        cwd=repo_dir,
        state=state,
    )
    if result.returncode == 0:
        return True, ""
    detail = (result.stderr or "").strip() or (result.stdout or "").strip()
    detail = detail or f"git worktree remove --force 失败（exit={result.returncode}）"
    return False, detail


def _cleanup_remote_branch(issue_number: int, repo_dir: Path, state: ExecState) -> tuple[bool, str]:
    branch = f"issue-{issue_number}"
    result = _run_capture(["git", "push", "origin", "--delete", branch], cwd=repo_dir, state=state)
    if result.returncode == 0:
        return True, ""

    detail = (result.stderr or "").strip() or (result.stdout or "").strip()
    detail_lower = detail.lower()
    if "remote ref does not exist" in detail_lower:
        return True, ""

    return False, detail or f"git push origin --delete {branch} 失败（exit={result.returncode}）"


def _cleanup_local_branch(issue_number: int, repo_dir: Path, state: ExecState) -> tuple[bool, str]:
    branch = f"issue-{issue_number}"
    result = _run_capture(["git", "branch", "-D", branch], cwd=repo_dir, state=state)
    if result.returncode == 0:
        return True, ""

    detail = (result.stderr or "").strip() or (result.stdout or "").strip()
    detail_lower = detail.lower()
    if "not found" in detail_lower and "branch" in detail_lower:
        return True, ""

    return False, detail or f"git branch -D {branch} 失败（exit={result.returncode}）"


def _cleanup_all_resources(state: ExecState, repo_dir: Path, worktree_script: Path) -> CleanupReport:
    report = CleanupReport()
    with state.lock:
        issue_numbers = sorted(state.created_issues)
        active_worktrees = dict(state.active_worktrees)

    report.tracked_issues = issue_numbers

    for issue_number in issue_numbers:
        # 1) 删除 worktree（失败则 --force）
        worktree_ok, worktree_detail = _remove_worktree(worktree_script, issue_number, repo_dir, state)
        worktree_detail_lower = (worktree_detail or "").lower()
        if not worktree_ok and "worktree not found" in worktree_detail_lower:
            worktree_ok, worktree_detail = True, ""

        if not worktree_ok:
            worktree_path = _get_worktree_path(worktree_script, issue_number, repo_dir, state)
            if not worktree_path:
                worktree_path = active_worktrees.get(issue_number)
            if worktree_path:
                report.worktree_force_used.add(issue_number)
                ok2, detail2 = _force_remove_worktree(issue_number, worktree_path, repo_dir, state)
                if ok2:
                    worktree_ok, worktree_detail = True, ""
                else:
                    merged = "; ".join(x for x in [worktree_detail, detail2] if x)
                    worktree_ok, worktree_detail = False, merged or worktree_detail or detail2

        report.worktree_removed[issue_number] = (worktree_ok, worktree_detail)

        # 2) 删除本地分支
        lb_ok, lb_detail = _cleanup_local_branch(issue_number, repo_dir, state)
        report.local_branch_deleted[issue_number] = (lb_ok, lb_detail)

        # 3) 删除远端分支
        rb_ok, rb_detail = _cleanup_remote_branch(issue_number, repo_dir, state)
        report.remote_branch_deleted[issue_number] = (rb_ok, rb_detail)

    # 4) 执行 git worktree prune
    prune = _run_capture(["git", "worktree", "prune"], cwd=repo_dir, state=state)
    if prune.returncode == 0:
        report.prune_ok = True
        report.prune_detail = ""
    else:
        detail = (prune.stderr or "").strip() or (prune.stdout or "").strip()
        report.prune_ok = False
        report.prune_detail = detail or f"git worktree prune 失败（exit={prune.returncode}）"

    return report


def _build_task_content(issue_number: int, title: str) -> str:
    """
    构建任务内容，确保特殊字符安全处理。

    Args:
        issue_number: Issue 编号
        title: Issue 标题

    Returns:
        格式化的任务内容字符串
    """
    # 对标题进行安全处理，移除可能导致问题的控制字符
    safe_title = title.replace("\r", "").replace("\0", "")

    return (
        f"实现 Issue #{issue_number}: {safe_title}\n\n"
        "Requirements:\n"
        "- 参考 issue 描述完成开发任务\n"
        f"- 创建 issue-{issue_number} 分支\n"
        "- 提交代码并创建 PR\n\n"
        "Deliverables:\n"
        "- 代码实现\n"
        "- 单元测试\n"
        f"- 创建 PR (分支名: issue-{issue_number})\n"
    )


def _build_codeagent_cmd(backend: str = "codex") -> list[str]:
    """
    使用安全方式构建 codeagent-wrapper 命令。

    Args:
        backend: 后端类型

    Returns:
        命令参数列表
    """
    if HAS_SAFE_COMMAND:
        builder = build_codeagent_command(backend=backend, use_stdin=True)
        return builder.build()
    else:
        # 回退到直接列表构造（仍然安全，因为不经过 shell）
        return ["codeagent-wrapper", "--backend", shlex.quote(backend), "-"]


def _run_claude(issue_number: int, title: str, worktree_path: Path, state: ExecState) -> int:
    """
    使用 codeagent-wrapper 执行 Issue 实现任务。

    通过 stdin 传递任务内容，避免 heredoc 和特殊字符问题。

    Args:
        issue_number: Issue 编号
        title: Issue 标题
        worktree_path: worktree 路径
        state: 执行状态

    Returns:
        进程返回码
    """
    task_content = _build_task_content(issue_number, title)

    # 使用列表模式构造命令，避免 shell 解析
    cmd = ["codeagent-wrapper", "--backend", "codex", "-"]

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(worktree_path),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        return 127

    state.current_process = proc
    state.last_process = proc
    try:
        # 通过 stdin.PIPE 传递内容，避免 heredoc 格式问题
        stdout, stderr = proc.communicate(input=task_content)
    finally:
        state.current_process = None

    session_id = _parse_session_id(stdout) or _parse_session_id(stderr)
    if session_id:
        with state.lock:
            state.session_ids[issue_number] = session_id
    return proc.returncode


def _get_pr_number(issue_number: int, repo: Optional[str], cwd: Path, state: ExecState) -> Optional[int]:
    cmd = ["gh", "pr", "list", "--head", f"issue-{issue_number}"]
    if repo:
        cmd += ["--repo", repo]
    cmd += ["--json", "number", "-q", ".[0].number"]

    result = _run_capture(cmd, cwd=cwd, state=state)
    if result.returncode != 0:
        detail = (result.stderr or "").strip() or (result.stdout or "").strip()
        raise RuntimeError(detail or f"gh pr list 失败（exit={result.returncode}）")

    raw = (result.stdout or "").strip()
    if not raw or raw == "null":
        return None
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return None


def _build_pr_review_content(pr_number: int) -> str:
    """
    构建 PR 审查任务内容。

    Args:
        pr_number: PR 编号

    Returns:
        格式化的任务内容字符串
    """
    return (
        f"审查并处理 PR #{pr_number}\n\n"
        "Requirements:\n"
        "- 检查 PR 代码质量\n"
        "- 运行相关测试\n"
        "- 如有问题，提供修复建议\n"
    )


def _run_pr_review(
    pr_number: int,
    worktree_path: Path,
    tty_stdin: Optional[TextIO],
    state: ExecState,
) -> int:
    """
    使用 codeagent-wrapper 执行 PR 审查任务。

    通过 stdin 传递任务内容，避免 heredoc 和特殊字符问题。

    Args:
        pr_number: PR 编号
        worktree_path: worktree 路径
        tty_stdin: TTY stdin（用于交互）
        state: 执行状态

    Returns:
        进程返回码
    """
    task_content = _build_pr_review_content(pr_number)

    # 使用列表模式构造命令，避免 shell 解析
    cmd = ["codeagent-wrapper", "--backend", "codex", "-"]

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(worktree_path),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        return 127

    state.current_process = proc
    state.last_process = proc
    try:
        # 通过 stdin.PIPE 传递内容，避免 heredoc 格式问题
        stdout, stderr = proc.communicate(input=task_content)
    finally:
        state.current_process = None

    return proc.returncode


def _merge_pr(pr_number: int, repo: Optional[str], cwd: Path, state: ExecState) -> tuple[bool, str]:
    cmd = ["gh", "pr", "merge", str(pr_number), "--squash", "--delete-branch", "--yes"]
    if repo:
        cmd += ["--repo", repo]

    result = _run_capture(cmd, cwd=cwd, state=state)
    if result.returncode == 0:
        return True, ""
    detail = (result.stderr or "").strip() or (result.stdout or "").strip()
    return False, detail or f"gh pr merge 失败（exit={result.returncode}）"


def _format_duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds or 0.0))
    whole = int(seconds + 0.5)  # round to nearest second
    minutes, sec = divmod(whole, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}h{minutes}m{sec}s"
    if minutes > 0:
        return f"{minutes}m{sec}s"
    return f"{sec}s"


def _print_report(results: list[IssueResult], interrupted: bool) -> None:
    total = len(results)
    completed = [r for r in results if r.status == "completed"]
    failed = [r for r in results if r.status == "failed"]
    skipped = [r for r in results if r.status == "skipped"]
    interrupted_issues = [r for r in results if r.status == "interrupted"]
    total_attempts = sum(r.attempts for r in results)
    total_retries = sum(max(0, r.attempts - 1) for r in results)
    retried = [r for r in results if r.attempts > 1]
    retried_ok = [r for r in results if r.status == "completed" and r.attempts > 1]
    retried_failed = [r for r in results if r.status == "failed" and r.attempts > 1]
    total_elapsed_sec = sum(r.elapsed_sec for r in results)

    print("\n完成报告:")
    if results:
        issue_col = "issue"
        title_col = "title"
        pr_col = "PR"
        status_col = "status"
        time_col = "time"
        max_title_width = 60

        table_rows: list[tuple[str, str, str, str, str]] = []
        for r in results:
            issue_val = f"#{r.number}"
            title_val = (r.title or "").strip() or "-"
            if len(title_val) > max_title_width:
                title_val = title_val[: max_title_width - 1] + "…"
            pr_val = f"#{r.pr_number}" if r.pr_number else "-"
            status_val = r.status
            time_val = _format_duration(r.elapsed_sec)
            table_rows.append((issue_val, title_val, pr_val, status_val, time_val))

        w_issue = max(len(issue_col), *(len(r[0]) for r in table_rows))
        w_title = max(len(title_col), *(len(r[1]) for r in table_rows))
        w_pr = max(len(pr_col), *(len(r[2]) for r in table_rows))
        w_status = max(len(status_col), *(len(r[3]) for r in table_rows))
        w_time = max(len(time_col), *(len(r[4]) for r in table_rows))

        def _row(cols: tuple[str, str, str, str, str]) -> str:
            c1, c2, c3, c4, c5 = cols
            return (
                f"{c1:<{w_issue}}  {c2:<{w_title}}  {c3:<{w_pr}}  {c4:<{w_status}}  {c5:>{w_time}}"
            )

        print(_row((issue_col, title_col, pr_col, status_col, time_col)))
        print(_row(("-" * w_issue, "-" * w_title, "-" * w_pr, "-" * w_status, "-" * w_time)))
        for row in table_rows:
            print(_row(row))

        print(f"\n- 总耗时: {_format_duration(total_elapsed_sec)}")
    print(f"- 总计: {total}")
    print(f"- 已完成: {len(completed)}")
    print(f"- 失败: {len(failed)}")
    print(f"- 总尝试次数: {total_attempts}")
    print(f"- 总重试次数: {total_retries}")
    print(f"- 触发重试的 Issue: {len(retried)}")
    if retried_ok:
        print(f"- 重试后成功: {len(retried_ok)}")
    if retried_failed:
        print(f"- 重试后仍失败: {len(retried_failed)}")
    if skipped:
        print(f"- 已跳过: {len(skipped)}")
    if interrupted or interrupted_issues:
        print("- 已中断: 是")

    if completed:
        nums = " ".join(f"#{r.number}" for r in completed)
        print(f"- 完成列表: {nums}")
    if failed:
        nums = " ".join(f"#{r.number}" for r in failed)
        print(f"- 失败列表: {nums}")
    if interrupted_issues:
        nums = " ".join(f"#{r.number}" for r in interrupted_issues)
        print(f"- 中断位置: {nums}")


def _print_cleanup_report(report: CleanupReport) -> None:
    print("\n===== Cleanup Report =====")

    total = len(report.tracked_issues)
    print(f"- 追踪 issues: {total}")
    if total == 0:
        print("- 无需清理")
        return

    worktree_ok = sum(1 for ok, _ in report.worktree_removed.values() if ok)
    local_ok = sum(1 for ok, _ in report.local_branch_deleted.values() if ok)
    remote_ok = sum(1 for ok, _ in report.remote_branch_deleted.values() if ok)

    print(f"- worktree 清理: {worktree_ok}/{total}")
    if report.worktree_force_used:
        forced = sorted(report.worktree_force_used)
        if len(forced) <= 20:
            nums = " ".join(f"#{n}" for n in forced)
            print(f"- worktree --force: {len(forced)} ({nums})")
        else:
            print(f"- worktree --force: {len(forced)}")
    print(f"- 本地分支删除: {local_ok}/{total}")
    print(f"- 远端分支删除: {remote_ok}/{total}")
    print(f"- git worktree prune: {'OK' if report.prune_ok else 'FAILED'}")
    if not report.prune_ok and report.prune_detail:
        print(f"  - {report.prune_detail}")

    def _print_failures(title: str, mapping: dict[int, tuple[bool, str]]) -> None:
        failed_items = [(i, d) for i, (ok, d) in mapping.items() if not ok]
        if not failed_items:
            return
        failed_items.sort(key=lambda x: x[0])
        print(f"- {title} 失败: {len(failed_items)}")
        for issue_number, detail in failed_items[:20]:
            suffix = f": {detail}" if detail else ""
            print(f"  - #{issue_number}{suffix}")
        if len(failed_items) > 20:
            print(f"  - ... 还有 {len(failed_items) - 20} 个")

    _print_failures("worktree 清理", report.worktree_removed)
    _print_failures("本地分支删除", report.local_branch_deleted)
    _print_failures("远端分支删除", report.remote_branch_deleted)


def _parse_issue_numbers_csv(value: str) -> list[int]:
    items = [x.strip() for x in (value or "").split(",")]
    parsed: list[int] = []
    for item in items:
        if not item:
            continue
        if not item.isdigit():
            raise ValueError(f"无效 issue 编号: {item}")
        num = int(item)
        if num <= 0:
            raise ValueError(f"无效 issue 编号: {item}")
        parsed.append(num)

    seen: set[int] = set()
    deduped: list[int] = []
    for num in parsed:
        if num in seen:
            continue
        seen.add(num)
        deduped.append(num)
    return deduped


def _extract_issue_numbers(text: str) -> set[int]:
    matches = ISSUE_BRANCH_PATTERN.findall(text or "")
    return {int(m) for m in matches if m.isdigit() and int(m) > 0}


def _collect_issue_numbers(repo_dir: Path, state: ExecState) -> set[int]:
    candidates: set[int] = set()
    cmds = [
        ["git", "branch", "--list", "issue-*"],
        ["git", "branch", "-r", "--list", "origin/issue-*"],
        ["git", "worktree", "list", "--porcelain"],
    ]
    for cmd in cmds:
        result = _run_capture(cmd, cwd=repo_dir, state=state)
        if result.returncode != 0:
            continue
        candidates |= _extract_issue_numbers(result.stdout or "")
    return candidates


def _get_default_base_ref(repo_dir: Path, state: ExecState) -> str:
    result = _run_capture(["git", "symbolic-ref", "refs/remotes/origin/HEAD"], cwd=repo_dir, state=state)
    if result.returncode == 0:
        ref = (result.stdout or "").strip()
        if ref:
            parts = [p for p in ref.split("/") if p]
            if len(parts) >= 2:
                return "/".join(parts[-2:])
    return "origin/main"


def _is_issue_merged_via_git(issue_number: int, repo_dir: Path, state: ExecState) -> tuple[Optional[bool], str]:
    base_ref = _get_default_base_ref(repo_dir, state)
    result = _run_capture(
        ["git", "branch", "--merged", base_ref, "--list", f"issue-{issue_number}"],
        cwd=repo_dir,
        state=state,
    )
    if result.returncode != 0:
        detail = (result.stderr or "").strip() or (result.stdout or "").strip()
        detail = detail or f"git branch --merged {base_ref} 失败（exit={result.returncode}）"
        return None, detail
    merged = bool((result.stdout or "").strip())
    if merged:
        return True, f"git: merged into {base_ref}"
    return False, ""


def _is_issue_merged_via_gh(
    issue_number: int,
    repo: Optional[str],
    repo_dir: Path,
    state: ExecState,
) -> tuple[Optional[bool], str]:
    cmd = ["gh", "pr", "list", "--state", "merged", "--head", f"issue-{issue_number}"]
    if repo:
        cmd += ["--repo", repo]
    cmd += ["--json", "number", "-q", ".[0].number"]

    result = _run_capture(cmd, cwd=repo_dir, state=state)
    if result.returncode == 0:
        raw = (result.stdout or "").strip()
        if raw and raw != "null":
            return True, ""
        return False, ""

    detail = (result.stderr or "").strip() or (result.stdout or "").strip()
    if result.returncode == 127:
        return None, detail or "gh 不存在"
    return None, detail or f"gh pr list 失败（exit={result.returncode}）"


def _is_issue_merged(
    issue_number: int,
    repo: Optional[str],
    repo_dir: Path,
    state: ExecState,
) -> tuple[Optional[bool], str]:
    merged, detail = _is_issue_merged_via_gh(issue_number, repo, repo_dir, state)
    if merged is not None:
        return merged, detail
    return _is_issue_merged_via_git(issue_number, repo_dir, state)


def cmd_cleanup(args, repo_dir: Path, worktree_script: Path) -> int:
    """
    手动清理 issue-* 相关资源。

    - 默认：清理所有已合并的 issue-* 分支
    - --cleanup-force：清理所有 issue-* 分支（不检查是否合并）
    - --cleanup-issues：仅清理指定 issue（逗号分隔）；与 --cleanup-force 同时使用时不检查是否合并
    """
    state = ExecState()
    started = time.monotonic()

    cleanup_force = bool(getattr(args, "cleanup_force", False))
    raw_issues = getattr(args, "cleanup_issues", None)
    specified: list[int] = []
    if raw_issues:
        try:
            specified = _parse_issue_numbers_csv(str(raw_issues))
        except ValueError as e:
            print(f"Error: --cleanup-issues 解析失败: {e}", file=sys.stderr)
            return 2

    candidates = set(specified) if specified else _collect_issue_numbers(repo_dir, state)
    candidates = {n for n in candidates if n > 0}

    print("\n🧹 手动清理: --cleanup", flush=True)
    mode = "force" if cleanup_force else "merged-only"
    if specified:
        print(f"- 模式: {mode} (指定 issues)", flush=True)
    else:
        print(f"- 模式: {mode}", flush=True)
    print(f"- 仓库目录: {repo_dir}", flush=True)
    print(f"- 候选 issues: {len(candidates)}", flush=True)

    if not candidates:
        print("- 无需清理", flush=True)
        return 0

    to_clean: list[int] = []
    skipped_not_merged: list[int] = []
    skipped_unknown: dict[int, str] = {}

    for issue_number in sorted(candidates):
        if cleanup_force:
            to_clean.append(issue_number)
            continue

        merged, detail = _is_issue_merged(issue_number, getattr(args, "repo", None), repo_dir, state)
        if merged is True:
            to_clean.append(issue_number)
            continue
        if merged is False:
            skipped_not_merged.append(issue_number)
            continue
        skipped_unknown[issue_number] = detail or "无法确认是否已合并"

    if skipped_not_merged:
        nums = " ".join(f"#{n}" for n in skipped_not_merged[:50])
        suffix = "" if len(skipped_not_merged) <= 50 else f" ...(+{len(skipped_not_merged) - 50})"
        print(f"- 跳过未合并: {len(skipped_not_merged)} ({nums}{suffix})", flush=True)

    if skipped_unknown:
        keys = sorted(skipped_unknown)[:20]
        nums = " ".join(f"#{n}" for n in keys)
        suffix = "" if len(skipped_unknown) <= 20 else f" ...(+{len(skipped_unknown) - 20})"
        print(f"- 跳过(状态未知): {len(skipped_unknown)} ({nums}{suffix})", flush=True)
        first = keys[0]
        print(f"  - 例: #{first}: {skipped_unknown[first]}", flush=True)

    print(f"- 将清理: {len(to_clean)}", flush=True)
    if not to_clean:
        print("- 无需清理", flush=True)
        return 0

    state.created_issues = set(to_clean)
    report = _cleanup_all_resources(state=state, repo_dir=repo_dir, worktree_script=worktree_script)
    _print_cleanup_report(report)

    failures = (
        sum(1 for ok, _ in report.worktree_removed.values() if not ok)
        + sum(1 for ok, _ in report.local_branch_deleted.values() if not ok)
        + sum(1 for ok, _ in report.remote_branch_deleted.values() if not ok)
        + (0 if report.prune_ok else 1)
    )
    elapsed = time.monotonic() - started
    print(f"\n- 清理耗时: {_format_duration(elapsed)}", flush=True)
    if failures:
        print(f"⚠️ 清理完成（失败项: {failures}）", flush=True)
        return 1
    print("✅ 清理完成", flush=True)
    return 0


# ==================== 并发执行核心函数 ====================

def _execute_single_issue(
    spec: IssueSpec,
    idx: int,
    total: int,
    prio_label: str,
    repo: Optional[str],
    repo_dir: Path,
    worktree_script: Path,
    max_retries: int,
    force_cleanup: bool,
    tty_stdin: Optional[TextIO],
    state: ExecState,
    print_lock: Lock,
) -> IssueResult:
    """
    执行单个 issue 的完整流程（worktree → codeagent-wrapper → PR review → merge）。
    线程安全，可用于并发执行。
    """
    issue_number = spec.number
    priority = spec.priority or "p2"
    title = spec.title or ""

    # 资源追踪：即使后续步骤失败也要记录
    with state.lock:
        state.created_issues.add(issue_number)

    issue_start = time.monotonic()
    observed_pr_number: Optional[int] = None
    last_error: str = ""

    # 如果没有 title，尝试获取
    if not title:
        title = _run_gh_issue_title(issue_number, repo, cwd=repo_dir) or ""
    title_display = title if title else "(无法获取标题)"

    with print_lock:
        print(
            f"[{idx}/{total}] 正在处理 Issue #{issue_number}: {title_display} ({prio_label})",
            flush=True,
        )

    # 注册到 state
    with state.lock:
        state.active_issues.add(issue_number)

    max_attempts = 1 + max_retries
    attempt_details: list[str] = []
    final_status = "failed"
    final_returncode: Optional[int] = None
    attempts = 0

    for attempt in range(1, max_attempts + 1):
        if state.interrupted:
            final_status = "interrupted"
            break

        attempts = attempt
        final_returncode = None
        worktree_path: Optional[Path] = None

        if attempt > 1:
            retry_idx = attempt - 1
            with print_lock:
                print(
                    f"🔄 Issue #{issue_number} 第 {retry_idx}/{max_retries} 次重试...",
                    flush=True,
                )

            # 清理之前的 worktree
            existing_path = _get_worktree_path(worktree_script, issue_number, repo_dir, state)
            if existing_path:
                ok, detail = _remove_worktree(worktree_script, issue_number, repo_dir, state)
                if not ok:
                    ok2, detail2 = _force_remove_worktree(issue_number, existing_path, repo_dir, state)
                    if ok2:
                        ok, detail = True, ""
                    else:
                        detail = f"{detail}; {detail2}"
                if not ok:
                    with print_lock:
                        print(f"Warning: worktree 清理失败: #{issue_number}: {detail}", file=sys.stderr)

            # 清理远程分支
            ok_rb, detail_rb = _cleanup_remote_branch(issue_number, repo_dir, state)
            if not ok_rb:
                with print_lock:
                    print(f"Warning: 远程分支清理失败: #{issue_number}: {detail_rb}", file=sys.stderr)

        try:
            worktree_path = _create_worktree(worktree_script, issue_number, repo_dir, state)
            with state.lock:
                state.active_worktrees[issue_number] = worktree_path

            rc = _run_claude(issue_number, title_display, worktree_path, state)
            if state.interrupted:
                final_status = "interrupted"
                final_returncode = rc
                break

            if rc == 0:
                pr_number = _get_pr_number(issue_number, repo, cwd=repo_dir, state=state)
                if pr_number:
                    observed_pr_number = pr_number
                    review_rc = _run_pr_review(pr_number, worktree_path, tty_stdin, state)
                    if review_rc != 0:
                        last_error = f"pr review exit={review_rc}"
                        attempt_details.append(f"attempt {attempt}: {last_error}")
                        final_returncode = review_rc
                    else:
                        ok, detail = _merge_pr(pr_number, repo, cwd=repo_dir, state=state)
                        if ok:
                            final_status = "completed"
                            final_returncode = rc
                            break
                        last_error = detail
                        attempt_details.append(f"attempt {attempt}: {detail}")
                else:
                    observed_pr_number = None
                    final_status = "completed"
                    final_returncode = rc
                    break
            else:
                last_error = f"codeagent exit={rc}"
                attempt_details.append(f"attempt {attempt}: {last_error}")
                final_returncode = rc

        except KeyboardInterrupt:
            state.interrupted = True
            final_status = "interrupted"
            break
        except Exception as e:
            last_error = str(e)
            attempt_details.append(f"attempt {attempt}: {e}")
        finally:
            if worktree_path:
                ok, detail = _remove_worktree(worktree_script, issue_number, repo_dir, state)
                if not ok and (force_cleanup or state.interrupted):
                    ok2, detail2 = _force_remove_worktree(issue_number, worktree_path, repo_dir, state)
                    if ok2:
                        ok, detail = True, ""
                    else:
                        detail = f"{detail}; {detail2}"
                if not ok:
                    with print_lock:
                        print(f"Warning: worktree 清理失败: #{issue_number}: {detail}", file=sys.stderr)

            with state.lock:
                state.active_worktrees.pop(issue_number, None)

        if final_status == "completed" or state.interrupted:
            break

    elapsed_sec = time.monotonic() - issue_start
    detail = "\n".join(attempt_details).strip()
    if final_status == "failed" and not last_error:
        last_error = _last_nonempty_line(detail) or "-"

    # 从 state 注销
    with state.lock:
        state.active_issues.discard(issue_number)

    result = IssueResult(
        number=issue_number,
        priority=priority,
        title=title,
        status=final_status,
        pr_number=observed_pr_number,
        elapsed_sec=elapsed_sec,
        attempts=attempts,
        returncode=final_returncode,
        detail=detail,
    )

    # 打印结果
    with print_lock:
        if result.status == "completed":
            pr_text = f"，PR #{result.pr_number} 已合并" if result.pr_number else ""
            print(
                f"✅ Issue #{issue_number} 已完成{pr_text} (耗时 {_format_duration(result.elapsed_sec)})",
                flush=True,
            )
        elif result.status == "failed":
            print(
                f"❌ Issue #{issue_number} 失败 (尝试 {attempts}/{max_attempts}): {last_error}",
                flush=True,
            )

    return result


def _execute_batch_concurrent(
    batch_specs: list[IssueSpec],
    batch_priority: str,
    start_idx: int,
    total: int,
    repo: Optional[str],
    repo_dir: Path,
    worktree_script: Path,
    max_retries: int,
    force_cleanup: bool,
    tty_stdin: Optional[TextIO],
    state: ExecState,
    results: list[IssueResult],
    results_lock: Lock,
) -> int:
    """
    并发执行一个批次内的所有 issues（DAG 调度，依赖感知）。
    返回完成的 issue 数量。
    """
    if not batch_specs:
        return 0

    prio_label = batch_priority.strip().upper() if batch_priority else "P2"

    # 检查是否有依赖
    has_dependencies = any(spec.dependencies for spec in batch_specs)

    # 计算自适应并发数
    max_workers = _calculate_max_workers(batch_priority, len(batch_specs), has_dependencies)

    print(f"📦 {prio_label} 批次 ({len(batch_specs)} issues, 并发={max_workers})", flush=True)

    # 创建 DAG 调度器
    scheduler = DagScheduler(batch_specs)
    print_lock = Lock()
    batch_completed = 0
    current_idx = start_idx

    # 创建 issue number -> spec 映射
    spec_map = {s.number: s for s in batch_specs}
    # 创建 issue number -> idx 映射
    idx_map = {}
    for i, spec in enumerate(batch_specs):
        idx_map[spec.number] = start_idx + i

    def execute_issue(issue_num: int) -> IssueResult:
        """执行单个 issue 的包装函数"""
        spec = spec_map[issue_num]
        idx = idx_map[issue_num]
        return _execute_single_issue(
            spec=spec,
            idx=idx,
            total=total,
            prio_label=prio_label,
            repo=repo,
            repo_dir=repo_dir,
            worktree_script=worktree_script,
            max_retries=max_retries,
            force_cleanup=force_cleanup,
            tty_stdin=tty_stdin,
            state=state,
            print_lock=print_lock,
        )

    # 使用 ThreadPoolExecutor 并发执行
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures: dict[int, Any] = {}  # issue_number -> Future

        while not scheduler.is_done() and not state.interrupted:
            # 获取可以开始的 issues
            ready_issues = scheduler.get_ready_issues()

            # 提交新任务
            for issue_num in ready_issues:
                if issue_num not in futures and scheduler.mark_started(issue_num):
                    future = executor.submit(execute_issue, issue_num)
                    futures[issue_num] = future

            # 检查已完成的任务
            completed_futures = []
            for issue_num, future in list(futures.items()):
                if future.done():
                    completed_futures.append((issue_num, future))

            for issue_num, future in completed_futures:
                try:
                    result = future.result()
                    with results_lock:
                        results.append(result)

                    if result.status == "completed":
                        scheduler.mark_completed(issue_num)
                        batch_completed += 1
                    else:
                        scheduler.mark_failed(issue_num)
                except Exception as e:
                    # 异常情况，标记为失败
                    scheduler.mark_failed(issue_num)
                    with print_lock:
                        print(f"❌ Issue #{issue_num} 执行异常: {e}", file=sys.stderr)

                del futures[issue_num]

            # 检查是否有被阻塞的 issues
            blocked = scheduler.has_blocked_issues()
            if blocked and not futures and not ready_issues:
                # 所有可执行的都完成了，但还有被阻塞的
                with print_lock:
                    blocked_nums = " ".join(f"#{n}" for n in blocked)
                    print(f"⚠️ 以下 issues 因依赖失败而跳过: {blocked_nums}", flush=True)

                # 标记被阻塞的 issues 为 skipped
                for issue_num in blocked:
                    scheduler.mark_failed(issue_num)
                    spec = spec_map[issue_num]
                    with results_lock:
                        results.append(IssueResult(
                            number=issue_num,
                            priority=spec.priority,
                            title=spec.title,
                            status="skipped",
                            detail="依赖的 issue 失败",
                        ))
                break

            # 短暂休眠避免 CPU 占用过高
            if futures:
                time.sleep(0.1)

        # 等待所有正在执行的任务完成（中断时也要等待）
        for issue_num, future in futures.items():
            try:
                result = future.result(timeout=1.0)
                with results_lock:
                    results.append(result)
                if result.status == "completed":
                    batch_completed += 1
            except Exception:
                pass

    if not state.interrupted:
        print(f"📦 {prio_label} 批次完成 ({batch_completed}/{len(batch_specs)})", flush=True)

    return batch_completed


def main() -> None:
    parser = argparse.ArgumentParser(description="按 priority 批次并发执行 Issues（worktree + codeagent-wrapper）")
    parser.add_argument("--input", help="priority_batcher.py --json 的输出文件（默认从 stdin 读取）")
    parser.add_argument("--repo", help="用于 gh issue view 的仓库（默认使用当前仓库）")
    parser.add_argument("--repo-dir", default=".", help="执行 git/gh/worktree 的仓库目录（默认当前目录）")
    parser.add_argument(
        "--worktree-script",
        default=str(DEFAULT_WORKTREE_SCRIPT),
        help="worktree.py 脚本路径",
    )
    parser.add_argument(
        "--force-cleanup",
        action="store_true",
        help="清理 worktree 失败时，尝试使用 git worktree remove --force",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="手动清理 issue-* 相关资源（默认仅清理已合并分支）",
    )
    parser.add_argument(
        "--cleanup-force",
        action="store_true",
        help="与 --cleanup 一起使用：清理所有 issue-* 分支（不检查是否合并）",
    )
    parser.add_argument(
        "--cleanup-issues",
        help="与 --cleanup 一起使用：仅清理指定 issue 编号列表（逗号分隔，如 123,124）",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="每个 issue 失败后的最大重试次数（默认 3）",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=0,
        help="覆盖自适应并发数，0 表示使用自适应计算（默认 0）",
    )
    args = parser.parse_args()

    repo_dir = Path(args.repo_dir).expanduser().resolve()
    worktree_script = Path(args.worktree_script).expanduser().resolve()

    if not worktree_script.exists():
        print(f"Error: worktree.py 不存在: {worktree_script}", file=sys.stderr)
        sys.exit(1)

    if args.cleanup:
        sys.exit(cmd_cleanup(args=args, repo_dir=repo_dir, worktree_script=worktree_script))

    if args.max_retries < 0:
        print("Error: --max-retries 必须 >= 0", file=sys.stderr)
        sys.exit(2)

    state = ExecState()
    results: list[IssueResult] = []
    results_lock = Lock()
    tty_stdin = _open_tty_stdin()

    def _handle_sigint(_signum, _frame):
        state.interrupted = True
        # 向所有活跃进程发送 SIGINT
        with state.lock:
            for proc in state.active_processes.values():
                if proc and proc.poll() is None:
                    try:
                        proc.send_signal(signal.SIGINT)
                    except Exception:
                        pass
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _handle_sigint)

    data = _read_json_input(args.input)
    upstream_warnings = data.get("warnings")
    if isinstance(upstream_warnings, list):
        for w in upstream_warnings:
            if isinstance(w, str) and w.strip():
                print(f"Warning: {w.strip()}", file=sys.stderr)
    specs, warnings = _extract_specs(data)
    if warnings:
        for w in warnings:
            print(f"Warning: {w}", file=sys.stderr)

    total = len(specs)
    if total == 0:
        _print_report([], interrupted=False)
        return

    print(f"🚀 开始处理 (共 {total} 个 issues)", flush=True)

    # 按优先级分组
    batches: list[tuple[str, list[IssueSpec]]] = []
    for spec in specs:
        priority = spec.priority or "p2"
        if not batches or batches[-1][0] != priority:
            batches.append((priority, [spec]))
        else:
            batches[-1][1].append(spec)

    try:
        idx = 1
        for batch_priority, batch_specs in batches:
            if state.interrupted:
                break

            # 并发执行批次
            _execute_batch_concurrent(
                batch_specs=batch_specs,
                batch_priority=batch_priority,
                start_idx=idx,
                total=total,
                repo=args.repo,
                repo_dir=repo_dir,
                worktree_script=worktree_script,
                max_retries=args.max_retries,
                force_cleanup=args.force_cleanup,
                tty_stdin=tty_stdin,
                state=state,
                results=results,
                results_lock=results_lock,
            )

            idx += len(batch_specs)

    except KeyboardInterrupt:
        state.interrupted = True
        # 清理所有活跃的 worktrees
        with state.lock:
            for issue_num, worktree_path in list(state.active_worktrees.items()):
                _force_remove_worktree(issue_num, worktree_path, repo_dir, state)

    finally:
        cleanup_report: Optional[CleanupReport] = None
        try:
            cleanup_report = _cleanup_all_resources(state=state, repo_dir=repo_dir, worktree_script=worktree_script)
        except Exception as e:
            print(f"Warning: 资源清理异常: {e}", file=sys.stderr)

        if tty_stdin:
            try:
                tty_stdin.close()
            except Exception:
                pass
        _print_report(results, interrupted=state.interrupted)
        if cleanup_report is not None:
            _print_cleanup_report(cleanup_report)

    if state.interrupted:
        sys.exit(130)
    if any(r.status == "failed" for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
