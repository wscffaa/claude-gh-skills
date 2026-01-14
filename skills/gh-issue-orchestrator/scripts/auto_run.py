#!/usr/bin/env python3
"""
自动执行脚本：串行处理 issues（独立会话 + worktree）

用法:
    python3 auto_run.py [--count N] [--dry-run]
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).parent
SKILL_DIR = SCRIPT_DIR.parent
LOG_FILE = SKILL_DIR / ".auto_run.log"


def log(msg: str):
    """记录日志"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def run_cmd(cmd: str, capture: bool = False, cwd: str = None) -> tuple[int, str]:
    """执行命令"""
    log(f"  $ {cmd}")
    result = subprocess.run(
        cmd, shell=True, capture_output=capture, text=True, cwd=cwd
    )
    if capture:
        return result.returncode, result.stdout.strip()
    return result.returncode, ""


def get_issues(count: int = None) -> list[int]:
    """获取待处理的 issues"""
    cmd = f"python3 {SCRIPT_DIR}/list_issues.py --mode auto"
    if count:
        cmd += f" --count {count}"
    _, output = run_cmd(cmd, capture=True)
    return json.loads(output)


def create_worktree(issue_number: int) -> str:
    """创建 worktree"""
    cmd = f"python3 {SCRIPT_DIR}/worktree.py create {issue_number}"
    _, output = run_cmd(cmd, capture=True)
    # 输出的最后一行是路径
    lines = output.strip().split("\n")
    return lines[-1]


def remove_worktree(issue_number: int):
    """删除 worktree"""
    cmd = f"python3 {SCRIPT_DIR}/worktree.py remove {issue_number}"
    run_cmd(cmd)


def get_pr_number(issue_number: int) -> int | None:
    """获取 issue 对应的 PR 编号"""
    cmd = f"gh pr list --head issue-{issue_number} --json number -q '.[0].number'"
    code, output = run_cmd(cmd, capture=True)
    if code == 0 and output:
        return int(output)
    return None


def process_issue(issue_number: int, dry_run: bool = False) -> dict:
    """处理单个 issue"""
    result = {
        "issue": issue_number,
        "branch": f"issue-{issue_number}",
        "pr": None,
        "status": "pending"
    }

    try:
        # Step 1: 创建 worktree
        log(f"[Issue #{issue_number}] 创建 worktree...")
        if dry_run:
            worktree_path = f"/tmp/dry-run/issue-{issue_number}"
            log(f"  [DRY-RUN] Would create: {worktree_path}")
        else:
            worktree_path = create_worktree(issue_number)
            log(f"  Worktree: {worktree_path}")

        # Step 2: 在 worktree 中实现 issue（独立会话）
        log(f"[Issue #{issue_number}] 启动独立会话实现 issue...")
        if dry_run:
            log(f"  [DRY-RUN] Would run: cd {worktree_path} && claude -p '/gh-issue-implement {issue_number}'")
        else:
            impl_cmd = f'cd "{worktree_path}" && claude -p "/gh-issue-implement {issue_number}"'
            code, _ = run_cmd(impl_cmd)
            if code != 0:
                result["status"] = "impl_failed"
                return result

        # Step 3: 获取 PR 编号
        log(f"[Issue #{issue_number}] 获取 PR 编号...")
        if dry_run:
            pr_number = 999
            log(f"  [DRY-RUN] Would get PR number")
        else:
            pr_number = get_pr_number(issue_number)
            if not pr_number:
                log(f"  警告: 未找到 PR")
                result["status"] = "no_pr"
                return result
            log(f"  PR #{pr_number}")
            result["pr"] = pr_number

        # Step 4: Review PR（独立会话）
        log(f"[Issue #{issue_number}] 启动独立会话 Review PR #{pr_number}...")
        if dry_run:
            log(f"  [DRY-RUN] Would run: claude -p '/gh-pr-review {pr_number}'")
        else:
            review_cmd = f'claude -p "/gh-pr-review {pr_number}"'
            code, _ = run_cmd(review_cmd)
            if code != 0:
                result["status"] = "review_failed"
                return result

        # Step 5: 清理 worktree
        log(f"[Issue #{issue_number}] 清理 worktree...")
        if dry_run:
            log(f"  [DRY-RUN] Would remove worktree")
        else:
            remove_worktree(issue_number)

        result["status"] = "completed"
        log(f"[Issue #{issue_number}] ✅ 完成")

    except Exception as e:
        log(f"[Issue #{issue_number}] ❌ 错误: {e}")
        result["status"] = "error"

    return result


def main():
    parser = argparse.ArgumentParser(description="自动执行 issues")
    parser.add_argument("--count", type=int, help="处理的 issue 数量")
    parser.add_argument("--dry-run", action="store_true", help="演示模式，不实际执行")
    args = parser.parse_args()

    log("=" * 60)
    log("GitHub Issue Orchestrator - 自动执行模式")
    log("=" * 60)

    # 获取 issues 列表
    issues = get_issues(args.count)
    if not issues:
        log("没有待处理的 issues")
        return

    log(f"待处理 issues: {issues}")
    log("")

    # 处理每个 issue
    results = []
    for issue_number in issues:
        log("-" * 40)
        result = process_issue(issue_number, args.dry_run)
        results.append(result)
        log("")

    # 输出总结报告
    log("=" * 60)
    log("自动化完成报告")
    log("=" * 60)
    log("")
    log("| Issue | Branch | PR | 状态 |")
    log("|-------|--------|-----|------|")

    completed = 0
    for r in results:
        status_icon = "✅" if r["status"] == "completed" else "❌"
        pr_str = f"#{r['pr']}" if r["pr"] else "-"
        log(f"| #{r['issue']} | {r['branch']} | {pr_str} | {status_icon} {r['status']} |")
        if r["status"] == "completed":
            completed += 1

    log("")
    log(f"共处理 {len(results)} 个 issues，完成 {completed} 个")


if __name__ == "__main__":
    main()
