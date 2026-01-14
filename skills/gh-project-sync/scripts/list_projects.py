#!/usr/bin/env python3
"""
列出 GitHub Projects，返回格式化选项供交互选择。

用法:
    python3 list_projects.py [--owner OWNER] [--json]

输出:
    默认: 格式化的项目列表
    --json: JSON 格式输出
"""

import argparse
import json
import subprocess
import sys
from typing import Optional


def get_repo_owner() -> Optional[str]:
    """从 git remote 获取仓库 owner。"""
    try:
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "owner", "-q", ".owner.login"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def list_projects(owner: Optional[str] = None) -> list[dict]:
    """
    获取 GitHub Projects 列表。

    Returns:
        list[dict]: 项目列表，每项包含 number, title, id, url
    """
    if not owner:
        owner = get_repo_owner()

    if not owner:
        print("Error: 无法确定仓库 owner，请使用 --owner 参数指定", file=sys.stderr)
        sys.exit(1)

    cmd = ["gh", "project", "list", "--owner", owner, "--format", "json"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            print(f"Error: {result.stderr}", file=sys.stderr)
            sys.exit(1)

        data = json.loads(result.stdout)

        # gh project list 返回格式: {"projects": [...]}
        projects = data.get("projects", [])

        # 标准化输出格式
        return [
            {
                "number": p.get("number"),
                "title": p.get("title"),
                "id": p.get("id"),
                "url": p.get("url", ""),
                "state": p.get("closed", False) and "closed" or "open",
            }
            for p in projects
            if not p.get("closed", False)  # 只返回 open 状态的项目
        ]

    except subprocess.TimeoutExpired:
        print("Error: gh 命令超时", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: JSON 解析失败: {e}", file=sys.stderr)
        sys.exit(1)


def format_project_options(projects: list[dict]) -> str:
    """
    格式化项目列表为交互选项。

    Returns:
        str: 格式化的选项列表
    """
    lines = []

    if projects:
        for i, p in enumerate(projects, 1):
            lines.append(f"{i}. {p['title']} (Project #{p['number']})")
    else:
        lines.append("(暂无已有项目)")

    lines.append(f"{len(projects) + 1}. [新建项目]")
    lines.append(f"{len(projects) + 2}. [跳过]")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="列出 GitHub Projects")
    parser.add_argument("--owner", help="仓库/组织 owner")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    args = parser.parse_args()

    projects = list_projects(args.owner)

    if args.json:
        # JSON 输出包含原始数据和选项索引
        output = {
            "projects": projects,
            "options": {
                "new_project_index": len(projects) + 1,
                "skip_index": len(projects) + 2,
            },
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print("请选择目标 GitHub Project：")
        print(format_project_options(projects))


if __name__ == "__main__":
    main()
