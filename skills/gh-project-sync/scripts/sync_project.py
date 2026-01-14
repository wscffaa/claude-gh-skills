#!/usr/bin/env python3
"""
同步 Issues 到 GitHub Project，支持优先级映射和 Epic 处理。

用法:
    python3 sync_project.py --project 1 --issues "63-71"
    python3 sync_project.py --project 1 --all
    python3 sync_project.py --project 1 --epic 72
    python3 sync_project.py --project 1 --issues "63" --json

功能:
    1. 批量添加 Issues 到 Project
    2. 根据优先级标签设置状态列
    3. 支持 Epic 及其 Sub-issues 的自动检测
"""

import argparse
import json
import re
import subprocess
import sys
from typing import Optional


# 优先级 → 状态列映射
PRIORITY_STATUS_MAP: dict[str, str] = {
    "priority:p0": "In Progress",
    "priority:p1": "Todo",
    "priority:p2": "Todo",
    "priority:p3": "Backlog",
}
DEFAULT_STATUS = "Todo"


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


def get_repo_name() -> Optional[str]:
    """获取仓库名称。"""
    try:
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "name", "-q", ".name"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def gh_api_graphql(query: str, variables: dict[str, str], timeout: int = 30) -> dict:
    """调用 gh api graphql，并返回 data 字段。"""
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    for k, v in variables.items():
        cmd.extend(["-F", f"{k}={v}"])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            print(f"Error: {result.stderr.strip()}", file=sys.stderr)
            return {}

        payload = json.loads(result.stdout)

        if payload.get("errors"):
            print(f"Error: GraphQL 请求失败: {payload['errors']}", file=sys.stderr)
            return {}

        return payload.get("data", {})

    except subprocess.TimeoutExpired:
        print("Error: gh api graphql 命令超时", file=sys.stderr)
        return {}
    except json.JSONDecodeError as e:
        print(f"Error: GraphQL JSON 解析失败: {e}", file=sys.stderr)
        return {}


def parse_issue_range(issues_str: str) -> list[int]:
    """
    解析 issue 范围字符串。

    支持格式:
    - "63,64,65" → [63, 64, 65]
    - "63-71" → [63, 64, 65, 66, 67, 68, 69, 70, 71]
    - "63-65,70,72-74" → [63, 64, 65, 70, 72, 73, 74]
    """
    result = []
    parts = issues_str.replace(" ", "").split(",")

    for part in parts:
        if not part:
            continue
        if "-" in part:
            try:
                start, end = part.split("-", 1)
                start_num = int(start.lstrip("#"))
                end_num = int(end.lstrip("#"))
                result.extend(range(start_num, end_num + 1))
            except ValueError:
                print(f"Warning: 无法解析范围 '{part}'", file=sys.stderr)
        else:
            try:
                result.append(int(part.lstrip("#")))
            except ValueError:
                print(f"Warning: 无法解析 Issue 编号 '{part}'", file=sys.stderr)

    return sorted(set(result))


def get_issue_details(issue_number: int) -> Optional[dict]:
    """获取 Issue 详情，包括标签和 body。"""
    try:
        result = subprocess.run(
            [
                "gh", "issue", "view", str(issue_number),
                "--json", "number,title,labels,body,url,state"
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except Exception as e:
        print(f"Warning: 获取 Issue #{issue_number} 详情失败: {e}", file=sys.stderr)
    return None


def get_priority_from_labels(labels: list[dict]) -> str:
    """从标签列表中提取优先级对应的状态列。"""
    for label in labels:
        label_name = label.get("name", "")
        if label_name in PRIORITY_STATUS_MAP:
            return PRIORITY_STATUS_MAP[label_name]
    return DEFAULT_STATUS


def is_epic(labels: list[dict]) -> bool:
    """检测是否为 Epic Issue。"""
    return any(label.get("name", "").lower() == "epic" for label in labels)


def extract_sub_issues(body: str) -> list[int]:
    """
    从 Issue body 中提取 Sub-issues。

    检测模式:
    - "Part of #N"
    - "Closes part of #N"
    - "- [ ] #N" (任务列表)
    """
    sub_issues = set()

    # Part of #N 或 Closes part of #N
    pattern1 = r"(?:Part of|Closes part of)\s*#(\d+)"
    for match in re.finditer(pattern1, body, re.IGNORECASE):
        sub_issues.add(int(match.group(1)))

    # 任务列表中的引用 - [ ] #N
    pattern2 = r"-\s*\[[ x]\]\s*#(\d+)"
    for match in re.finditer(pattern2, body, re.IGNORECASE):
        sub_issues.add(int(match.group(1)))

    # 直接引用 #N (在 Sub-issues 或 Tasks 标题下)
    pattern3 = r"(?:Sub-issues|Tasks|子任务)[:\s]*(?:[\s\S]*?)#(\d+)"
    for match in re.finditer(pattern3, body, re.IGNORECASE):
        sub_issues.add(int(match.group(1)))

    return sorted(sub_issues)


def get_all_open_issues() -> list[int]:
    """获取所有 Open 状态的 Issue 编号。"""
    try:
        result = subprocess.run(
            ["gh", "issue", "list", "--state", "open", "--json", "number", "-q", ".[].number"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            numbers = result.stdout.strip().split("\n")
            return [int(n) for n in numbers if n.strip()]
    except Exception as e:
        print(f"Error: 获取 Open Issues 失败: {e}", file=sys.stderr)
    return []


def get_project_info(owner: str, project_number: int) -> Optional[dict]:
    """获取 Project 详情，包括 ID 和 Status 字段信息。"""
    query = """
    query($owner: String!, $number: Int!) {
      user(login: $owner) {
        projectV2(number: $number) {
          id
          title
          url
          fields(first: 20) {
            nodes {
              ... on ProjectV2SingleSelectField {
                id
                name
                options {
                  id
                  name
                }
              }
            }
          }
        }
      }
    }
    """

    data = gh_api_graphql(query, {"owner": owner, "number": str(project_number)})

    user = data.get("user")
    if not user:
        # 尝试 organization
        query_org = query.replace("user(login: $owner)", "organization(login: $owner)")
        data = gh_api_graphql(query_org, {"owner": owner, "number": str(project_number)})
        user = data.get("organization")

    if not user:
        return None

    project = user.get("projectV2")
    if not project:
        return None

    # 提取 Status 字段
    status_field = None
    for field in project.get("fields", {}).get("nodes", []):
        if field and field.get("name", "").lower() == "status":
            status_field = field
            break

    return {
        "id": project["id"],
        "title": project["title"],
        "url": project["url"],
        "status_field": status_field,
    }


def add_issue_to_project(owner: str, project_number: int, issue_url: str) -> Optional[str]:
    """添加 Issue 到 Project，返回 Item ID。"""
    try:
        result = subprocess.run(
            [
                "gh", "project", "item-add", str(project_number),
                "--owner", owner,
                "--url", issue_url,
                "--format", "json"
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get("id")
        else:
            # 可能已存在，尝试获取 Item ID
            if "already exists" in result.stderr.lower():
                return get_item_id_for_issue(owner, project_number, issue_url)
            print(f"Warning: 添加失败: {result.stderr.strip()}", file=sys.stderr)
    except Exception as e:
        print(f"Warning: 添加 Issue 到 Project 失败: {e}", file=sys.stderr)
    return None


def get_item_id_for_issue(owner: str, project_number: int, issue_url: str) -> Optional[str]:
    """获取已存在的 Issue 在 Project 中的 Item ID。"""
    try:
        result = subprocess.run(
            [
                "gh", "project", "item-list", str(project_number),
                "--owner", owner,
                "--format", "json"
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            for item in data.get("items", []):
                content = item.get("content", {})
                if content.get("url") == issue_url:
                    return item.get("id")
    except Exception:
        pass
    return None


def set_item_status(project_id: str, item_id: str, field_id: str, option_id: str) -> bool:
    """设置 Project Item 的状态。"""
    mutation = """
    mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
      updateProjectV2ItemFieldValue(input: {
        projectId: $projectId,
        itemId: $itemId,
        fieldId: $fieldId,
        value: { singleSelectOptionId: $optionId }
      }) {
        projectV2Item {
          id
        }
      }
    }
    """

    data = gh_api_graphql(mutation, {
        "projectId": project_id,
        "itemId": item_id,
        "fieldId": field_id,
        "optionId": option_id,
    })

    return bool(data.get("updateProjectV2ItemFieldValue"))


def sync_issues_to_project(
    owner: str,
    project_number: int,
    issue_numbers: list[int],
    json_output: bool = False
) -> dict:
    """
    同步 Issues 到 Project。

    Returns:
        dict: 同步结果统计
    """
    # 获取 Project 信息
    project_info = get_project_info(owner, project_number)
    if not project_info:
        print(f"Error: 无法获取 Project #{project_number} 信息", file=sys.stderr)
        sys.exit(1)

    project_id = project_info["id"]
    status_field = project_info.get("status_field")

    # 构建状态选项映射
    status_option_map = {}
    if status_field:
        for option in status_field.get("options", []):
            status_option_map[option["name"]] = option["id"]

    # 获取仓库信息构建 Issue URL
    repo_name = get_repo_name()
    if not repo_name:
        print("Error: 无法获取仓库名称", file=sys.stderr)
        sys.exit(1)

    results = []
    status_counts = {"In Progress": 0, "Todo": 0, "Backlog": 0, "Review": 0, "Done": 0}

    for issue_num in issue_numbers:
        issue = get_issue_details(issue_num)
        if not issue:
            results.append({
                "issue": issue_num,
                "status": "error",
                "message": "获取详情失败"
            })
            continue

        issue_url = issue.get("url") or f"https://github.com/{owner}/{repo_name}/issues/{issue_num}"
        labels = issue.get("labels", [])
        target_status = get_priority_from_labels(labels)

        # 添加到 Project
        item_id = add_issue_to_project(owner, project_number, issue_url)
        if not item_id:
            results.append({
                "issue": issue_num,
                "title": issue.get("title", ""),
                "status": "error",
                "message": "添加到 Project 失败"
            })
            continue

        # 设置状态列
        status_set = False
        if status_field and target_status in status_option_map:
            status_set = set_item_status(
                project_id,
                item_id,
                status_field["id"],
                status_option_map[target_status]
            )

        if status_set:
            status_counts[target_status] = status_counts.get(target_status, 0) + 1

        results.append({
            "issue": issue_num,
            "title": issue.get("title", ""),
            "status_column": target_status,
            "status": "success" if status_set else "partial",
        })

    output = {
        "project": {
            "number": project_number,
            "title": project_info["title"],
            "url": project_info["url"],
        },
        "synced": len([r for r in results if r["status"] in ("success", "partial")]),
        "failed": len([r for r in results if r["status"] == "error"]),
        "status_counts": {k: v for k, v in status_counts.items() if v > 0},
        "results": results,
    }

    return output


def print_results(output: dict, json_output: bool = False) -> None:
    """打印同步结果。"""
    if json_output:
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    project = output["project"]
    print(f"\n✅ 已同步 {output['synced']} 个 Issues 到 Project \"{project['title']}\"")

    if output["failed"] > 0:
        print(f"❌ 失败: {output['failed']} 个")

    print()
    print("| Issue | 标题 | 状态列 |")
    print("|-------|------|--------|")

    for r in output["results"]:
        status_icon = "✅" if r["status"] == "success" else ("⚠️" if r["status"] == "partial" else "❌")
        title = r.get("title", "")[:40]
        status_col = r.get("status_column", r.get("message", ""))
        print(f"| #{r['issue']} | {title} | {status_icon} {status_col} |")

    print()

    if output["status_counts"]:
        counts = output["status_counts"]
        parts = [f"{k}: {v}" for k, v in counts.items()]
        print(f"📊 状态分布: {', '.join(parts)}")

    print(f"\n🔗 Project URL: {project['url']}")


def main():
    parser = argparse.ArgumentParser(description="同步 Issues 到 GitHub Project")
    parser.add_argument("--project", "-p", type=int, required=True, help="Project 编号")
    parser.add_argument("--issues", "-i", help="Issue 编号范围 (如 '63-71' 或 '63,64,65')")
    parser.add_argument("--all", action="store_true", help="同步所有 Open Issues")
    parser.add_argument("--epic", "-e", type=int, help="Epic Issue 编号，自动包含 Sub-issues")
    parser.add_argument("--owner", help="仓库/组织 owner")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    args = parser.parse_args()

    owner = args.owner or get_repo_owner()
    if not owner:
        print("Error: 无法确定仓库 owner，请使用 --owner 参数指定", file=sys.stderr)
        sys.exit(1)

    # 确定要同步的 Issues
    issue_numbers = []

    if args.epic:
        # Epic 模式：获取 Epic 及其 Sub-issues
        epic_details = get_issue_details(args.epic)
        if not epic_details:
            print(f"Error: 无法获取 Epic #{args.epic} 详情", file=sys.stderr)
            sys.exit(1)

        issue_numbers.append(args.epic)

        # 提取 Sub-issues
        body = epic_details.get("body", "")
        sub_issues = extract_sub_issues(body)
        issue_numbers.extend(sub_issues)

        if not args.json:
            print(f"📦 Epic #{args.epic} 包含 {len(sub_issues)} 个 Sub-issues")

    elif args.all:
        # 所有 Open Issues
        issue_numbers = get_all_open_issues()
        if not args.json:
            print(f"📋 找到 {len(issue_numbers)} 个 Open Issues")

    elif args.issues:
        # 指定范围
        issue_numbers = parse_issue_range(args.issues)

    else:
        print("Error: 请指定 --issues、--all 或 --epic 参数", file=sys.stderr)
        parser.print_help()
        sys.exit(1)

    if not issue_numbers:
        print("Warning: 没有找到要同步的 Issues", file=sys.stderr)
        sys.exit(0)

    issue_numbers = sorted(set(issue_numbers))

    if not args.json:
        print(f"🔄 正在同步 {len(issue_numbers)} 个 Issues 到 Project #{args.project}...")

    # 执行同步
    output = sync_issues_to_project(
        owner=owner,
        project_number=args.project,
        issue_numbers=issue_numbers,
        json_output=args.json,
    )

    print_results(output, args.json)


if __name__ == "__main__":
    main()
