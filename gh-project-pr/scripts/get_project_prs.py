#!/usr/bin/env python3
"""
从 GitHub Project 获取 Issues 并查找关联的 PR。

用法:
    python3 get_project_prs.py --project 1 [--user] [--owner OWNER] [--json]

功能:
    Phase 1: 获取 Project Items，过滤 Issue 类型且非 Done 状态
    Phase 2: 对每个 Issue 尝试 3 种方式查找 PR:
        1. gh pr list --search "linked:issue:<N>"
        2. gh pr list --head "feat/issue-<N>"
        3. gh pr list --search "Closes #<N> in:body"

输出格式 (--json):
    {
      "mappings": [
        {"issue": 108, "pr": 112, "title": "...", "state": "open", "priority": "p0"}
      ],
      "stats": {
        "total_issues": 4,
        "with_pr": 3,
        "without_pr": 1,
        "pr_open": 2,
        "pr_merged": 1
      }
    }
"""

import argparse
import json
import re
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


def get_repo_full_name() -> Optional[str]:
    """获取完整的 owner/repo 名称。"""
    try:
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _run_gh(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """运行 gh 命令并返回 (returncode, stdout, stderr)。"""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "command timed out"
    except Exception as e:
        return -1, "", str(e)


def _run_gh_json(cmd: list[str], timeout: int = 30) -> Optional[dict | list]:
    """运行 gh 命令并解析 JSON 输出，失败返回 None。"""
    returncode, stdout, stderr = _run_gh(cmd, timeout)
    if returncode != 0:
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None


def list_project_items(owner: str, project_number: int) -> list[dict]:
    """获取 Project items 列表。"""
    data = _run_gh_json(
        [
            "gh",
            "project",
            "item-list",
            str(project_number),
            "--owner",
            owner,
            "--format",
            "json",
            "--limit",
            "1000",
        ],
        timeout=60,
    )

    if data is None:
        print(f"Error: 无法获取 Project #{project_number} 的 items", file=sys.stderr)
        sys.exit(1)

    items = data.get("items", []) if isinstance(data, dict) else []
    return items if isinstance(items, list) else []


def extract_priority(labels: list[str]) -> Optional[str]:
    """从 labels 中提取 priority（p0/p1/p2/p3），无则返回 None。"""
    best_rank: Optional[int] = None
    for label in labels:
        m = re.match(r"^priority:p([0-3])$", label)
        if not m:
            continue
        rank = int(m.group(1))
        if best_rank is None or rank < best_rank:
            best_rank = rank
    return f"p{best_rank}" if best_rank is not None else None


def find_pr_by_linked_issue(repo: str, issue_number: int) -> Optional[dict]:
    """方式 1: 使用 linked:issue 搜索关联 PR。"""
    data = _run_gh_json(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo,
            "--search",
            f"linked:issue:{issue_number}",
            "--json",
            "number,title,state,mergedAt",
            "--limit",
            "10",
        ],
        timeout=30,
    )
    if data and isinstance(data, list) and len(data) > 0:
        return data[0]
    return None


def find_pr_by_branch_pattern(repo: str, issue_number: int) -> Optional[dict]:
    """方式 2: 按分支名 feat/issue-<N> 搜索 PR。"""
    # 尝试多种常见分支命名模式
    patterns = [
        f"feat/issue-{issue_number}",
        f"feature/issue-{issue_number}",
        f"fix/issue-{issue_number}",
        f"issue-{issue_number}",
        f"{issue_number}",
    ]

    for pattern in patterns:
        data = _run_gh_json(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repo,
                "--head",
                pattern,
                "--state",
                "all",
                "--json",
                "number,title,state,mergedAt",
                "--limit",
                "5",
            ],
            timeout=30,
        )
        if data and isinstance(data, list) and len(data) > 0:
            return data[0]
    return None


def find_pr_by_body_reference(repo: str, issue_number: int) -> Optional[dict]:
    """方式 3: 搜索 body 中包含 Closes #N 的 PR。"""
    # 搜索常见的关闭关键词
    search_terms = [
        f"Closes #{issue_number}",
        f"Fixes #{issue_number}",
        f"Resolves #{issue_number}",
    ]

    for term in search_terms:
        data = _run_gh_json(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repo,
                "--search",
                f'"{term}" in:body',
                "--state",
                "all",
                "--json",
                "number,title,state,mergedAt",
                "--limit",
                "10",
            ],
            timeout=30,
        )
        if data and isinstance(data, list) and len(data) > 0:
            return data[0]
    return None


def find_pr_for_issue(repo: str, issue_number: int) -> Optional[dict]:
    """依次尝试 3 种方式查找 Issue 关联的 PR。"""
    # 方式 1: linked:issue
    pr = find_pr_by_linked_issue(repo, issue_number)
    if pr:
        return pr

    # 方式 2: 分支名
    pr = find_pr_by_branch_pattern(repo, issue_number)
    if pr:
        return pr

    # 方式 3: body 引用
    pr = find_pr_by_body_reference(repo, issue_number)
    if pr:
        return pr

    return None


def get_pr_state(pr: dict) -> str:
    """获取 PR 的状态: open/merged/closed。"""
    if pr.get("mergedAt"):
        return "merged"
    state = pr.get("state", "").lower()
    if state == "merged":
        return "merged"
    return state if state else "unknown"


def filter_project_issues(items: list[dict]) -> list[dict]:
    """
    Phase 1: 过滤 Project Items。

    条件:
    - 类型为 Issue
    - 状态不是 Done
    """
    filtered = []
    seen: set[tuple[str, int]] = set()

    for item in items:
        content = (item or {}).get("content") or {}
        if (content.get("type") or "") != "Issue":
            continue

        issue_number = content.get("number")
        issue_title = str(content.get("title", ""))
        issue_repo = content.get("repository", "")

        if not isinstance(issue_number, int) or not issue_repo:
            continue

        key = (issue_repo, issue_number)
        if key in seen:
            continue
        seen.add(key)

        # 检查 Project 状态字段（不是 Done）
        status = (item or {}).get("status") or ""
        if isinstance(status, str) and status.lower() == "done":
            continue

        labels = (item or {}).get("labels") or []
        if not isinstance(labels, list):
            labels = []
        labels = [str(l) for l in labels]

        filtered.append(
            {
                "number": issue_number,
                "title": issue_title,
                "repo": issue_repo,
                "labels": labels,
                "priority": extract_priority(labels),
            }
        )

    return filtered


def main():
    parser = argparse.ArgumentParser(description="获取 GitHub Project Issues 及关联 PR")
    parser.add_argument("--project", type=int, required=True, help="Project 编号（必填）")
    parser.add_argument(
        "--user",
        action="store_true",
        help="使用用户级 Project（默认为仓库级，但查询逻辑相同）",
    )
    parser.add_argument("--owner", help="用户/组织 owner（默认从当前仓库推断）")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    args = parser.parse_args()

    # 确定 owner
    owner = args.owner or get_repo_owner()
    if not owner:
        print("Error: 无法确定 owner，请使用 --owner 参数指定", file=sys.stderr)
        sys.exit(1)

    # Phase 1: 获取 Project Items 并过滤
    if not args.json:
        print(f"正在获取 Project #{args.project} 的 Items...", file=sys.stderr)

    items = list_project_items(owner, args.project)
    issues = filter_project_issues(items)

    if not args.json:
        print(f"找到 {len(issues)} 个非 Done 状态的 Issues", file=sys.stderr)

    # Phase 2: 查找关联 PR
    mappings: list[dict] = []
    stats = {
        "total_issues": len(issues),
        "with_pr": 0,
        "without_pr": 0,
        "pr_open": 0,
        "pr_merged": 0,
        "pr_closed": 0,
    }

    for i, issue in enumerate(issues, 1):
        if not args.json:
            print(
                f"[{i}/{len(issues)}] 查找 Issue #{issue['number']} 的 PR...",
                file=sys.stderr,
            )

        pr = find_pr_for_issue(issue["repo"], issue["number"])

        mapping = {
            "issue": issue["number"],
            "pr": None,
            "title": issue["title"],
            "state": None,
            "priority": issue["priority"],
        }

        if pr:
            state = get_pr_state(pr)
            mapping["pr"] = pr["number"]
            mapping["pr_title"] = pr.get("title", "")
            mapping["state"] = state
            stats["with_pr"] += 1

            if state == "open":
                stats["pr_open"] += 1
            elif state == "merged":
                stats["pr_merged"] += 1
            elif state == "closed":
                stats["pr_closed"] += 1
        else:
            stats["without_pr"] += 1

        mappings.append(mapping)

    # 输出结果
    output = {"mappings": mappings, "stats": stats}

    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    # 格式化输出
    print(f"\n{'='*60}")
    print(f"Project #{args.project} Issue-PR 映射")
    print(f"{'='*60}\n")

    # 统计
    print(f"统计:")
    print(f"  总 Issues: {stats['total_issues']}")
    print(f"  有 PR: {stats['with_pr']}")
    print(f"  无 PR: {stats['without_pr']}")
    print(f"  PR Open: {stats['pr_open']}")
    print(f"  PR Merged: {stats['pr_merged']}")
    print(f"  PR Closed: {stats['pr_closed']}")
    print()

    # 映射列表
    print("映射列表:")
    print("-" * 60)

    for m in mappings:
        prio_str = f"[{m['priority']}]" if m["priority"] else "[--]"
        if m["pr"]:
            state_icon = {"open": "O", "merged": "M", "closed": "X"}.get(
                m["state"], "?"
            )
            print(
                f"  Issue #{m['issue']:>4} -> PR #{m['pr']:>4} ({state_icon}) {prio_str} {m['title'][:40]}"
            )
        else:
            print(f"  Issue #{m['issue']:>4} -> (no PR)      {prio_str} {m['title'][:40]}")


if __name__ == "__main__":
    main()
