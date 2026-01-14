#!/usr/bin/env python3
"""
自动模式状态管理脚本

用法:
    python3 auto_state.py start [--count N]  # 启动自动模式
    python3 auto_state.py stop               # 停止自动模式
    python3 auto_state.py status             # 查看状态
    python3 auto_state.py next               # 获取下一个要处理的 issue
    python3 auto_state.py complete N         # 标记 issue N 完成
"""

import argparse
import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent / "hooks" / "gh-issue-orchestrator"
if not SCRIPT_DIR.exists():
    SCRIPT_DIR = Path(__file__).parent
STATE_FILE = SCRIPT_DIR / ".auto_state"
QUEUE_FILE = SCRIPT_DIR / ".auto_queue"
COMPLETED_FILE = SCRIPT_DIR / ".auto_completed"


def start_auto(issues: list[int]):
    """启动自动模式"""
    STATE_FILE.write_text("auto")
    QUEUE_FILE.write_text(json.dumps(issues))
    COMPLETED_FILE.write_text(json.dumps([]))
    print(f"自动模式已启动，待处理 {len(issues)} 个 issues: {issues}")


def stop_auto():
    """停止自动模式"""
    for f in [STATE_FILE, QUEUE_FILE, COMPLETED_FILE]:
        if f.exists():
            f.unlink()
    print("自动模式已停止")


def get_status() -> dict:
    """获取当前状态"""
    if not STATE_FILE.exists():
        return {"mode": "manual", "queue": [], "completed": []}

    queue = json.loads(QUEUE_FILE.read_text()) if QUEUE_FILE.exists() else []
    completed = json.loads(COMPLETED_FILE.read_text()) if COMPLETED_FILE.exists() else []

    return {
        "mode": STATE_FILE.read_text().strip(),
        "queue": queue,
        "completed": completed,
    }


def get_next() -> int | None:
    """获取下一个待处理的 issue"""
    status = get_status()
    if status["mode"] != "auto":
        return None

    queue = status["queue"]
    completed = status["completed"]

    for issue in queue:
        if issue not in completed:
            return issue
    return None


def complete_issue(issue_number: int):
    """标记 issue 完成"""
    status = get_status()
    completed = status["completed"]

    if issue_number not in completed:
        completed.append(issue_number)
        COMPLETED_FILE.write_text(json.dumps(completed))

    # 检查是否全部完成
    queue = status["queue"]
    remaining = [i for i in queue if i not in completed]

    if not remaining:
        print(f"所有 issues 已完成！共 {len(completed)} 个")
        stop_auto()
    else:
        print(f"Issue #{issue_number} 已完成，剩余 {len(remaining)} 个: {remaining}")


def main():
    parser = argparse.ArgumentParser(description="自动模式状态管理")
    parser.add_argument("action", choices=["start", "stop", "status", "next", "complete"])
    parser.add_argument("--issues", type=str, help="JSON 格式的 issue 列表")
    parser.add_argument("--count", type=int, help="issue 数量限制")
    parser.add_argument("issue_number", nargs="?", type=int, help="issue 编号 (complete 时使用)")

    args = parser.parse_args()

    if args.action == "start":
        if args.issues:
            issues = json.loads(args.issues)
        else:
            # 从 list_issues.py 获取
            import subprocess
            cmd = ["python3", str(Path(__file__).parent / "list_issues.py"), "--mode", "auto"]
            if args.count:
                cmd.extend(["--count", str(args.count)])
            result = subprocess.run(cmd, capture_output=True, text=True)
            issues = json.loads(result.stdout)
        start_auto(issues)

    elif args.action == "stop":
        stop_auto()

    elif args.action == "status":
        status = get_status()
        print(json.dumps(status, indent=2))

    elif args.action == "next":
        next_issue = get_next()
        if next_issue:
            print(next_issue)
        else:
            sys.exit(1)

    elif args.action == "complete":
        if args.issue_number:
            complete_issue(args.issue_number)
        else:
            print("需要指定 issue 编号", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
