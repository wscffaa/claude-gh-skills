#!/usr/bin/env python3
"""
根据 priority_batcher.py --json 的输出，按批次串行执行 issue。

功能:
- 从 stdin 或 --input 文件读取 JSON（priority_batcher.py --json 输出）
- 遍历每个批次与 issue，串行执行
- 复用 worktree.py 脚本进行 worktree 管理（默认路径: ~/.claude/skills/gh-issue-orchestrator/scripts/worktree.py）
- 每个 issue 创建独立 worktree: {repo}-worktrees/issue-{number}
- 使用 subprocess 启动独立 Claude 会话: claude -p "/gh-issue-implement {number}"
- 失败支持重试：清理 worktree 与远程分支后重试（--max-retries）
- 若检测到对应 PR（head=issue-{number}），自动执行 PR Review（claude -p "/gh-pr-review {pr_number}"）并合并（gh pr merge --squash --delete-branch）
- 等待独立会话完成后再处理下一个 issue
- issue 完成后自动清理 worktree
- Ctrl+C（SIGINT）时清理当前 worktree 并输出已完成报告

输出格式:
- 开始处理: 🚀 开始处理 (共 {total} 个 issues)
- 每个批次开始: 📦 {PRIORITY} 批次 ({count} issues)
- 每个 issue 开始: [2/10] 正在处理 Issue #42: xxx (P1)
- 每个 issue 完成: ✅ Issue #42 已完成，PR #123 已合并 (耗时 2m30s)
- 每个 issue 失败: ❌ Issue #42 失败 (尝试 2/4): xxx
- 每个批次完成: 📦 {PRIORITY} 批次完成 ({completed}/{total})
- 最终输出完成报告
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, TextIO


DEFAULT_WORKTREE_SCRIPT = Path("~/.claude/skills/gh-issue-orchestrator/scripts/worktree.py").expanduser()


@dataclass
class IssueSpec:
    number: int
    priority: str


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
            if isinstance(raw, int):
                number = raw
            elif isinstance(raw, str) and raw.strip().isdigit():
                number = int(raw.strip())

            if not number or number <= 0:
                continue
            if number in seen:
                warnings.append(f"重复 issue: #{number} 已跳过重复条目")
                continue
            seen.add(number)
            specs.append(IssueSpec(number=number, priority=priority or "p2"))

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


def _run_claude(issue_number: int, worktree_path: Path, tty_stdin: Optional[TextIO], state: ExecState) -> int:
    cmd = ["claude", "-p", f"/gh-issue-implement {issue_number}"]
    try:
        proc = subprocess.Popen(cmd, cwd=str(worktree_path), stdin=tty_stdin)
    except FileNotFoundError:
        return 127

    state.current_process = proc
    state.last_process = proc
    try:
        while True:
            try:
                return proc.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                continue
    finally:
        state.current_process = None


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


def _run_pr_review(
    pr_number: int,
    worktree_path: Path,
    tty_stdin: Optional[TextIO],
    state: ExecState,
) -> int:
    cmd = ["claude", "-p", f"/gh-pr-review {pr_number}"]
    try:
        proc = subprocess.Popen(cmd, cwd=str(worktree_path), stdin=tty_stdin)
    except FileNotFoundError:
        return 127

    state.current_process = proc
    state.last_process = proc
    try:
        while True:
            try:
                return proc.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                continue
    finally:
        state.current_process = None


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


def main() -> None:
    parser = argparse.ArgumentParser(description="按 priority 批次串行执行 Issues（worktree + claude）")
    parser.add_argument("--input", help="priority_batcher.py --json 的输出文件（默认从 stdin 读取）")
    parser.add_argument("--repo", help="用于 gh issue view 的仓库（默认使用当前仓库）")
    parser.add_argument("--repo-dir", default=".", help="执行 git/gh/worktree 的仓库目录（默认当前目录）")
    parser.add_argument(
        "--worktree-script",
        default=str(DEFAULT_WORKTREE_SCRIPT),
        help="worktree.py 脚本路径（默认使用 ~/.claude/skills/gh-issue-orchestrator/scripts/worktree.py）",
    )
    parser.add_argument(
        "--force-cleanup",
        action="store_true",
        help="清理 worktree 失败时，尝试使用 git worktree remove --force",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="每个 issue 失败后的最大重试次数（默认 3）",
    )
    args = parser.parse_args()

    repo_dir = Path(args.repo_dir).expanduser().resolve()
    worktree_script = Path(args.worktree_script).expanduser().resolve()

    if args.max_retries < 0:
        print("Error: --max-retries 必须 >= 0", file=sys.stderr)
        sys.exit(2)

    if not worktree_script.exists():
        print(f"Error: worktree.py 不存在: {worktree_script}", file=sys.stderr)
        sys.exit(1)

    state = ExecState()
    results: list[IssueResult] = []
    tty_stdin = _open_tty_stdin()

    def _handle_sigint(_signum, _frame):
        state.interrupted = True
        if state.current_process and state.current_process.poll() is None:
            try:
                state.current_process.send_signal(signal.SIGINT)
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

    batches: list[tuple[str, list[IssueSpec]]] = []
    for spec in specs:
        priority = spec.priority or "p2"
        if not batches or batches[-1][0] != priority:
            batches.append((priority, [spec]))
        else:
            batches[-1][1].append(spec)

    try:
        idx = 0
        for batch_priority, batch_specs in batches:
            if state.interrupted:
                break

            prio_label = batch_priority.strip().upper() if batch_priority else "P2"
            print(f"📦 {prio_label} 批次 ({len(batch_specs)} issues)", flush=True)

            batch_completed = 0
            for spec in batch_specs:
                idx += 1
                if state.interrupted:
                    break

                issue_number = spec.number
                priority = spec.priority or "p2"

                issue_start = time.monotonic()
                observed_pr_number: Optional[int] = None
                last_error: str = ""

                title = _run_gh_issue_title(issue_number, args.repo, cwd=repo_dir) or ""
                title_display = title if title else "(无法获取标题)"

                print(
                    f"[{idx}/{total}] 正在处理 Issue #{issue_number}: {title_display} ({prio_label})",
                    flush=True,
                )

                state.current_issue = issue_number
                max_attempts = 1 + args.max_retries
                attempt_details: list[str] = []
                final_status = "failed"
                final_returncode: Optional[int] = None
                attempts = 0

                for attempt in range(1, max_attempts + 1):
                    attempts = attempt
                    final_returncode = None
                    worktree_path: Optional[Path] = None

                    if attempt > 1:
                        retry_idx = attempt - 1
                        print(
                            f"🔄 Issue #{issue_number} 第 {retry_idx}/{args.max_retries} 次重试...",
                            flush=True,
                        )

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
                                print(f"Warning: worktree 清理失败: #{issue_number}: {detail}", file=sys.stderr)

                        ok_rb, detail_rb = _cleanup_remote_branch(issue_number, repo_dir, state)
                        if not ok_rb:
                            print(f"Warning: 远程分支清理失败: #{issue_number}: {detail_rb}", file=sys.stderr)

                    try:
                        worktree_path = _create_worktree(worktree_script, issue_number, repo_dir, state)
                        state.current_worktree_path = worktree_path

                        rc = _run_claude(issue_number, worktree_path, tty_stdin, state)
                        if state.interrupted:
                            final_status = "interrupted"
                            final_returncode = rc
                            break

                        if rc == 0:
                            pr_number = _get_pr_number(issue_number, args.repo, cwd=repo_dir, state=state)
                            if pr_number:
                                observed_pr_number = pr_number
                                review_rc = _run_pr_review(pr_number, worktree_path, tty_stdin, state)
                                if review_rc != 0:
                                    last_error = f"pr review exit={review_rc}"
                                    attempt_details.append(f"attempt {attempt}: {last_error}")
                                    final_returncode = review_rc
                                else:
                                    ok, detail = _merge_pr(pr_number, args.repo, cwd=repo_dir, state=state)
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
                            last_error = f"claude exit={rc}"
                            attempt_details.append(f"attempt {attempt}: {last_error}")
                            final_returncode = rc

                    except KeyboardInterrupt:
                        state.interrupted = True
                        proc = state.current_process or state.last_process
                        if proc and proc.poll() is None:
                            _stop_process(proc, timeout_sec=2.0)
                        if not worktree_path:
                            worktree_path = _get_worktree_path(worktree_script, issue_number, repo_dir, state)
                        final_status = "interrupted"
                        break
                    except Exception as e:
                        last_error = str(e)
                        attempt_details.append(f"attempt {attempt}: {e}")
                    finally:
                        if worktree_path:
                            ok, detail = _remove_worktree(worktree_script, issue_number, repo_dir, state)
                            if not ok and (args.force_cleanup or state.interrupted):
                                ok2, detail2 = _force_remove_worktree(issue_number, worktree_path, repo_dir, state)
                                if ok2:
                                    ok, detail = True, ""
                                else:
                                    detail = f"{detail}; {detail2}"
                            if not ok:
                                print(f"Warning: worktree 清理失败: #{issue_number}: {detail}", file=sys.stderr)

                        state.current_worktree_path = None

                    if final_status == "completed" or state.interrupted:
                        break

                    if attempt < max_attempts:
                        continue

                elapsed_sec = time.monotonic() - issue_start
                detail = "\n".join(attempt_details).strip()
                if final_status == "failed" and not last_error:
                    last_error = _last_nonempty_line(detail) or "-"

                results.append(
                    IssueResult(
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
                )

                state.current_issue = None

                if results and results[-1].number == issue_number and results[-1].status == "completed":
                    pr_text = f"，PR #{results[-1].pr_number} 已合并" if results[-1].pr_number else ""
                    print(
                        f"✅ Issue #{issue_number} 已完成{pr_text} (耗时 {_format_duration(results[-1].elapsed_sec)})",
                        flush=True,
                    )
                    batch_completed += 1
                elif results and results[-1].number == issue_number and results[-1].status == "failed":
                    print(
                        f"❌ Issue #{issue_number} 失败 (尝试 {attempts}/{max_attempts}): {last_error}",
                        flush=True,
                    )

                if state.interrupted:
                    break

                time.sleep(0.1)

            if not state.interrupted:
                print(f"📦 {prio_label} 批次完成 ({batch_completed}/{len(batch_specs)})", flush=True)
            else:
                break

    except KeyboardInterrupt:
        state.interrupted = True
        proc = state.current_process or state.last_process
        if proc and proc.poll() is None:
            _stop_process(proc, timeout_sec=2.0)

        issue_number = state.current_issue
        worktree_path = state.current_worktree_path
        if issue_number and not worktree_path:
            worktree_path = _get_worktree_path(worktree_script, issue_number, repo_dir, state)
        if issue_number and worktree_path:
            _force_remove_worktree(issue_number, worktree_path, repo_dir, state)

    finally:
        if tty_stdin:
            try:
                tty_stdin.close()
            except Exception:
                pass
        _print_report(results, interrupted=state.interrupted)

    if state.interrupted:
        sys.exit(130)
    if any(r.status == "failed" for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
