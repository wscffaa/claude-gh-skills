#!/usr/bin/env python3
"""
从指定 GitHub Project 获取所有 Open 状态的 Issues，并过滤掉已有 open PR 的条目。

用法:
    python3 get_project_issues.py --project 1 [--user] [--owner OWNER] [--json]

示例:
    # 默认使用当前仓库 owner 的 Project
    python3 get_project_issues.py --project 1 --json

    # 显式指定用户级 Project（与默认行为相同，保留用于 API 一致性）
    python3 get_project_issues.py --project 2 --user --json
    python3 get_project_issues.py --project 2 --owner monalisa --json

输出:
    默认: 格式化的 Issue 列表
    --json: JSON 格式输出，结构如下:
        {
          "project": {"number": 1, "title": "Sprint 1"},
          "issues": [
            {"number": 42, "title": "xxx", "labels": ["priority:p0"], "priority": "p0"}
          ]
        }

注意:
    GitHub Projects v2 的 CLI (gh project) 使用 --owner 参数指定用户名/组织名。
    仓库级和用户级 Project 在查询时使用相同的 owner（用户名）。
    --user 标志保留用于与 create_project.py 的 API 一致性。
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


def _run_gh_json(cmd: list[str], timeout: int = 30) -> dict:
    """运行 gh 命令并解析 JSON 输出。"""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print("Error: gh 命令超时", file=sys.stderr)
        sys.exit(1)

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        if stderr:
            print(f"Error: {stderr}", file=sys.stderr)
        else:
            print("Error: gh 命令执行失败", file=sys.stderr)
        sys.exit(1)

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"Error: JSON 解析失败: {e}", file=sys.stderr)
        sys.exit(1)


def get_project_info(owner: str, project_number: int) -> dict:
    """获取 Project 信息（number/title）。Project 不存在时退出。"""
    try:
        result = subprocess.run(
            ["gh", "project", "view", str(project_number), "--owner", owner, "--format", "json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        print("Error: gh project view 命令超时", file=sys.stderr)
        sys.exit(1)

    if result.returncode != 0:
        msg = (result.stderr or "").strip() or (result.stdout or "").strip()
        detail = f": {msg}" if msg else ""
        print(
            f"Error: Project #{project_number} 不存在或无法访问（owner={owner}）{detail}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"Error: Project JSON 解析失败: {e}", file=sys.stderr)
        sys.exit(1)

    return {"number": project_number, "title": data.get("title", "")}


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

    items = data.get("items", [])
    return items if isinstance(items, list) else []


def get_issue_state(repo: str, issue_number: int) -> Optional[str]:
    """获取 Issue state（OPEN/CLOSED）。失败返回 None。"""
    cmd = ["gh", "issue", "view", str(issue_number), "--repo", repo, "--json", "state", "-q", ".state"]

    for attempt in range(2):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except subprocess.TimeoutExpired:
            if attempt == 0:
                continue
            print(f"Warning: 获取 Issue 状态超时: {repo}#{issue_number}", file=sys.stderr)
        except Exception as e:
            print(f"Warning: 获取 Issue 状态失败: {repo}#{issue_number}: {e}", file=sys.stderr)
            return None

    return None


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


def get_open_pr_closing_issues(repo: str) -> set[int]:
    """获取指定仓库中所有 open PR 关联的 closing issues 编号集合。"""
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repo,
                "--state",
                "open",
                "--json",
                "closingIssuesReferences",
                "--limit",
                "1000",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        print(f"Error: gh pr list 命令超时（repo={repo}）", file=sys.stderr)
        sys.exit(1)

    if result.returncode != 0:
        msg = (result.stderr or "").strip() or (result.stdout or "").strip()
        detail = f": {msg}" if msg else ""
        print(f"Error: 获取 open PR 列表失败（repo={repo}）{detail}", file=sys.stderr)
        sys.exit(1)

    try:
        prs = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"Error: PR JSON 解析失败（repo={repo}）: {e}", file=sys.stderr)
        sys.exit(1)

    closing: set[int] = set()
    if isinstance(prs, list):
        for pr in prs:
            refs = (pr or {}).get("closingIssuesReferences") or []
            if not isinstance(refs, list):
                continue
            for issue in refs:
                n = (issue or {}).get("number")
                if isinstance(n, int):
                    closing.add(n)

    return closing


def main():
    parser = argparse.ArgumentParser(description="获取 GitHub Project 下所有 Open Issues")
    parser.add_argument("--project", type=int, required=True, help="Project 编号（必填）")
    parser.add_argument("--user", action="store_true", help="使用用户级 Project（默认为仓库级，但查询逻辑相同）")
    parser.add_argument("--owner", help="用户/组织 owner（默认从当前仓库推断）")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    args = parser.parse_args()

    # 确定 owner：gh project 命令只接受用户名/组织名
    owner = args.owner or get_repo_owner()
    if not owner:
        print("Error: 无法确定 owner，请使用 --owner 参数指定", file=sys.stderr)
        sys.exit(1)

    project = get_project_info(owner, args.project)
    items = list_project_items(owner, args.project)

    seen: set[tuple[str, int]] = set()
    candidates: list[dict] = []
    repos: set[str] = set()

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

        state = get_issue_state(issue_repo, issue_number)
        if not state or state.upper() != "OPEN":
            continue

        labels = (item or {}).get("labels") or []
        if not isinstance(labels, list):
            labels = []
        labels = [str(l) for l in labels]

        candidates.append(
            {
                "number": issue_number,
                "title": issue_title,
                "labels": labels,
                "priority": extract_priority(labels),
                "_repo": issue_repo,
            }
        )
        repos.add(issue_repo)

    closing_by_repo: dict[str, set[int]] = {r: get_open_pr_closing_issues(r) for r in sorted(repos)}

    issues = [
        {k: v for k, v in issue.items() if k != "_repo"}
        for issue in candidates
        if issue["number"] not in closing_by_repo.get(issue["_repo"], set())
    ]

    if args.json:
        print(json.dumps({"project": project, "issues": issues}, ensure_ascii=False, indent=2))
        return

    print(f"Project #{project['number']}: {project['title']}")
    print(f"Issues (Open, no open PR): {len(issues)}")
    for issue in issues:
        prio = issue.get("priority")
        prio_str = f"[{prio}] " if prio else ""
        print(f"- #{issue['number']} {prio_str}{issue['title']}")


if __name__ == "__main__":
    main()
