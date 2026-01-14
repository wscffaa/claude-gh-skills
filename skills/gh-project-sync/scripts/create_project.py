#!/usr/bin/env python3
"""
创建 GitHub Project，并初始化状态列。

用法:
    python3 create_project.py --title "名称" [--owner OWNER] [--json]
    python3 create_project.py --default [--owner OWNER] [--json]

功能:
    1. 使用 gh project create 创建新 GitHub Project
    2. 通过 GraphQL API 配置 5 个状态列：Backlog, Todo, In Progress, Review, Done

输出:
    默认: 创建结果（人类可读）
    --json: JSON 格式输出 {number, id, title, url}
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from typing import Optional


STATUS_OPTIONS: list[tuple[str, str]] = [
    ("Backlog", "GRAY"),
    ("Todo", "BLUE"),
    ("In Progress", "YELLOW"),
    ("Review", "PURPLE"),
    ("Done", "GREEN"),
]


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


def build_default_title() -> str:
    """生成带时间戳的默认 Project 标题。"""
    return f"New Project {datetime.now().strftime('%Y%m%d-%H%M%S')}"


def build_single_select_options_literal(options: list[tuple[str, str]]) -> str:
    """
    构建 GraphQL singleSelectOptions 字面量。

    GitHub GraphQL 要求每个选项必须包含 name/color/description 且均为非空字段。
    """
    parts = [
        f'{{name: "{name}", color: {color}, description: ""}}'
        for name, color in options
    ]
    return "[" + ", ".join(parts) + "]"


def gh_api_graphql(query: str, variables: dict[str, str], timeout: int = 30) -> dict:
    """调用 gh api graphql，并返回 data 字段。"""
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    for k, v in variables.items():
        cmd.extend(["-F", f"{k}={v}"])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            print(f"Error: {result.stderr.strip()}", file=sys.stderr)
            sys.exit(1)

        payload = json.loads(result.stdout)

        if payload.get("errors"):
            print(f"Error: GraphQL 请求失败: {payload['errors']}", file=sys.stderr)
            sys.exit(1)

        return payload.get("data", {})

    except subprocess.TimeoutExpired:
        print("Error: gh api graphql 命令超时", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: GraphQL JSON 解析失败: {e}", file=sys.stderr)
        sys.exit(1)


def create_project(owner: str, title: str) -> dict:
    """
    使用 gh project create 创建新 Project。

    Returns:
        dict: {number, id, title, url}
    """
    cmd = [
        "gh",
        "project",
        "create",
        "--owner",
        owner,
        "--title",
        title,
        "--format",
        "json",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            print(f"Error: {result.stderr.strip()}", file=sys.stderr)
            sys.exit(1)

        data = json.loads(result.stdout)

        project = {
            "number": data.get("number"),
            "id": data.get("id"),
            "title": data.get("title"),
            "url": data.get("url"),
        }

        if not project["id"] or project["number"] is None or not project["title"] or not project["url"]:
            print(f"Error: gh project create 返回字段缺失: {data}", file=sys.stderr)
            sys.exit(1)

        return project

    except subprocess.TimeoutExpired:
        print("Error: gh project create 命令超时", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: JSON 解析失败: {e}", file=sys.stderr)
        sys.exit(1)


def get_status_field_id(project_id: str) -> Optional[str]:
    """获取 Project 的 Status(single-select) 字段 ID。"""
    query = """
    query($id: ID!) {
      node(id: $id) {
        ... on ProjectV2 {
          fields(first: 100) {
            nodes {
              ... on ProjectV2SingleSelectField {
                id
                name
              }
            }
          }
        }
      }
    }
    """

    data = gh_api_graphql(query, {"id": project_id})
    node = data.get("node") or {}
    fields = (node.get("fields") or {}).get("nodes") or []

    candidates: list[str] = []
    for f in fields:
        field_id = f.get("id")
        if field_id:
            candidates.append(field_id)

        if (f.get("name") or "").strip().lower() == "status":
            return field_id

    if len(candidates) == 1:
        return candidates[0]

    return None


def create_status_field(project_id: str, options_literal: str) -> str:
    """创建 Status(single-select) 字段并返回字段 ID。"""
    mutation = f"""
    mutation($projectId: ID!) {{
      createProjectV2Field(input: {{
        projectId: $projectId,
        dataType: SINGLE_SELECT,
        name: "Status",
        singleSelectOptions: {options_literal}
      }}) {{
        projectV2Field {{
          ... on ProjectV2SingleSelectField {{
            id
            name
          }}
        }}
      }}
    }}
    """

    data = gh_api_graphql(mutation, {"projectId": project_id})
    field = ((data.get("createProjectV2Field") or {}).get("projectV2Field")) or {}

    field_id = field.get("id")
    if not field_id:
        print(f"Error: 创建 Status 字段失败: {data}", file=sys.stderr)
        sys.exit(1)

    return field_id


def update_status_field(field_id: str, options_literal: str) -> None:
    """更新 Status(single-select) 字段的选项列表。"""
    mutation = f"""
    mutation($fieldId: ID!) {{
      updateProjectV2Field(input: {{
        fieldId: $fieldId,
        singleSelectOptions: {options_literal}
      }}) {{
        projectV2Field {{
          ... on ProjectV2SingleSelectField {{
            id
            name
          }}
        }}
      }}
    }}
    """

    gh_api_graphql(mutation, {"fieldId": field_id})


def configure_status_columns(project_id: str) -> None:
    """确保 Project 存在 Status 字段，并将其选项设置为固定 5 列。"""
    options_literal = build_single_select_options_literal(STATUS_OPTIONS)

    field_id = get_status_field_id(project_id)
    if not field_id:
        field_id = create_status_field(project_id, options_literal)

    update_status_field(field_id, options_literal)


def main():
    parser = argparse.ArgumentParser(description="创建 GitHub Project 并初始化状态列")
    title_group = parser.add_mutually_exclusive_group(required=True)
    title_group.add_argument("--title", help="Project 标题")
    title_group.add_argument("--default", action="store_true", help="使用时间戳作为默认标题")
    parser.add_argument("--owner", help="仓库/组织 owner")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    args = parser.parse_args()

    owner = args.owner or get_repo_owner()
    if not owner:
        print("Error: 无法确定仓库 owner，请使用 --owner 参数指定", file=sys.stderr)
        sys.exit(1)

    title = args.title.strip() if args.title else ""
    if args.default:
        title = build_default_title()
    elif not title:
        print("Error: --title 不能为空", file=sys.stderr)
        sys.exit(1)

    project = create_project(owner, title)
    configure_status_columns(project["id"])

    if args.json:
        print(json.dumps(project, ensure_ascii=False, indent=2))
    else:
        print("Project 创建完成：")
        print(f"- {project['title']} (Project #{project['number']})")
        print(f"- {project['url']}")


if __name__ == "__main__":
    main()
