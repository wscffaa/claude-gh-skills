#!/usr/bin/env python3
"""
Git Worktree 管理脚本

用法:
    python3 worktree.py create <issue_number>   # 创建 worktree
    python3 worktree.py remove <issue_number>   # 删除 worktree
    python3 worktree.py list                    # 列出所有 worktrees
    python3 worktree.py cleanup                 # 清理已合并的 worktrees
    python3 worktree.py path <issue_number>     # 获取 worktree 路径
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def get_repo_root() -> Path:
    """获取仓库根目录"""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print("Error: Not in a git repository", file=sys.stderr)
        sys.exit(1)
    return Path(result.stdout.strip())


def get_worktree_base() -> Path:
    """获取 worktree 基础目录"""
    repo_root = get_repo_root()
    base = repo_root.parent / f"{repo_root.name}-worktrees"
    base.mkdir(exist_ok=True)
    return base


def get_main_branch() -> str:
    """获取主分支名称"""
    result = subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        # refs/remotes/origin/main -> main
        return result.stdout.strip().split("/")[-1]
    return "main"


def create_worktree(issue_number: int) -> Path:
    """为 issue 创建 worktree"""
    base = get_worktree_base()
    branch_name = f"issue-{issue_number}"
    worktree_path = base / branch_name

    if worktree_path.exists():
        print(f"Worktree already exists: {worktree_path}")
        return worktree_path

    main_branch = get_main_branch()

    # 先 fetch 最新代码
    subprocess.run(["git", "fetch", "origin", main_branch], check=True)

    # 创建新分支并设置 worktree
    result = subprocess.run(
        ["git", "worktree", "add", "-b", branch_name, str(worktree_path), f"origin/{main_branch}"],
        capture_output=True, text=True
    )

    if result.returncode != 0:
        # 分支可能已存在，尝试直接使用
        result = subprocess.run(
            ["git", "worktree", "add", str(worktree_path), branch_name],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"Error creating worktree: {result.stderr}", file=sys.stderr)
            sys.exit(1)

    print(f"Created worktree: {worktree_path}")
    print(f"Branch: {branch_name}")
    return worktree_path


def remove_worktree(issue_number: int):
    """删除 worktree"""
    base = get_worktree_base()
    branch_name = f"issue-{issue_number}"
    worktree_path = base / branch_name

    if not worktree_path.exists():
        print(f"Worktree not found: {worktree_path}")
        return

    # 删除 worktree
    subprocess.run(["git", "worktree", "remove", str(worktree_path)], check=True)

    # 可选：删除分支（如果已合并）
    result = subprocess.run(
        ["git", "branch", "-d", branch_name],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"Deleted branch: {branch_name}")

    print(f"Removed worktree: {worktree_path}")


def list_worktrees():
    """列出所有 worktrees"""
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        capture_output=True, text=True
    )

    worktrees = []
    current = {}

    for line in result.stdout.strip().split("\n"):
        if not line:
            if current:
                worktrees.append(current)
                current = {}
        elif line.startswith("worktree "):
            current["path"] = line[9:]
        elif line.startswith("branch "):
            current["branch"] = line[7:].split("/")[-1]
        elif line == "bare":
            current["bare"] = True

    if current:
        worktrees.append(current)

    # 过滤出 issue-* 分支的 worktrees
    issue_worktrees = [w for w in worktrees if w.get("branch", "").startswith("issue-")]

    if not issue_worktrees:
        print("No issue worktrees found")
        return

    print(f"Issue Worktrees ({len(issue_worktrees)}):\n")
    for wt in issue_worktrees:
        issue_num = wt["branch"].replace("issue-", "")
        print(f"  #{issue_num}: {wt['path']}")


def cleanup_worktrees():
    """清理已合并的 worktrees"""
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        capture_output=True, text=True
    )

    cleaned = 0
    for line in result.stdout.strip().split("\n"):
        if line.startswith("worktree "):
            path = line[9:]
            if "issue-" in path:
                # 检查分支是否已合并
                branch = Path(path).name
                merge_check = subprocess.run(
                    ["git", "branch", "--merged", "origin/main"],
                    capture_output=True, text=True
                )
                if branch in merge_check.stdout:
                    subprocess.run(["git", "worktree", "remove", path])
                    subprocess.run(["git", "branch", "-d", branch])
                    print(f"Cleaned up: {path}")
                    cleaned += 1

    print(f"\nCleaned {cleaned} worktrees")


def get_worktree_path(issue_number: int) -> str:
    """获取 worktree 路径"""
    base = get_worktree_base()
    branch_name = f"issue-{issue_number}"
    worktree_path = base / branch_name

    if worktree_path.exists():
        return str(worktree_path)
    return ""


def main():
    parser = argparse.ArgumentParser(description="Git Worktree Manager")
    parser.add_argument("action", choices=["create", "remove", "list", "cleanup", "path"])
    parser.add_argument("issue_number", nargs="?", type=int, help="Issue number")

    args = parser.parse_args()

    if args.action == "create":
        if not args.issue_number:
            print("Error: issue_number required", file=sys.stderr)
            sys.exit(1)
        path = create_worktree(args.issue_number)
        print(path)

    elif args.action == "remove":
        if not args.issue_number:
            print("Error: issue_number required", file=sys.stderr)
            sys.exit(1)
        remove_worktree(args.issue_number)

    elif args.action == "list":
        list_worktrees()

    elif args.action == "cleanup":
        cleanup_worktrees()

    elif args.action == "path":
        if not args.issue_number:
            print("Error: issue_number required", file=sys.stderr)
            sys.exit(1)
        path = get_worktree_path(args.issue_number)
        if path:
            print(path)
        else:
            sys.exit(1)


if __name__ == "__main__":
    main()
