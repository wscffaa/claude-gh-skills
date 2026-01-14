#!/usr/bin/env python3
"""
同步 Issue 状态到 GitHub Project（ProjectV2）看板。

功能:
- 获取 Project 的 Status 字段 ID 与 option IDs
- 找到指定 Issue 对应的 Project item ID
- 更新 item 的 Status 值为指定状态

命令行示例:
  python3 status_sync.py --project 1 --issue 42 --status "In Progress"
  python3 status_sync.py --project 1 --issue 42 --status "Done"
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from typing import Any, Callable, Literal, Optional


ProjectStatus = Literal["In Progress", "Done", "Failed"]
ALLOWED_STATUS: set[str] = {"In Progress", "Done", "Failed"}


def get_repo_owner() -> Optional[str]:
    """从当前仓库推断 owner（gh repo view）。"""
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


def _is_rate_limit_error(text: str) -> bool:
    msg = (text or "").lower()
    return any(
        key in msg
        for key in (
            "rate limit",
            "secondary rate limit",
            "too many requests",
            "abuse detection",
            "temporarily blocked",
        )
    )


def _sleep_backoff(attempt: int, on_warning: Optional[Callable[[str], None]]) -> None:
    # 5s, 10s, 20s
    delay = 5 * (2**attempt)
    if on_warning:
        on_warning(f"命中 API rate limit，{delay}s 后重试（{attempt + 1}/3）")
    time.sleep(delay)


def gh_api_graphql(
    query: str,
    variables: dict[str, Any],
    *,
    timeout: int = 30,
    max_retries: int = 3,
    on_warning: Optional[Callable[[str], None]] = None,
) -> tuple[dict[str, Any], str]:
    """调用 gh api graphql，并返回 (data, error)。遇到 rate limit 时自动退避重试。"""
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    for k, v in variables.items():
        cmd.extend(["-F", f"{k}={v}"])

    last_error = ""
    for attempt in range(max_retries):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            last_error = "gh api graphql 命令超时"
            break
        except FileNotFoundError:
            last_error = "未找到 gh 命令，请先安装并登录 GitHub CLI（gh auth login）"
            break
        except Exception as e:
            last_error = f"gh api graphql 调用失败: {e}"
            break

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()

        if result.returncode != 0:
            last_error = stderr or stdout or "gh api graphql 执行失败"
            if _is_rate_limit_error(last_error) and attempt < max_retries - 1:
                _sleep_backoff(attempt, on_warning)
                continue
            return {}, last_error

        try:
            payload = json.loads(stdout) if stdout else {}
        except json.JSONDecodeError as e:
            return {}, f"GraphQL JSON 解析失败: {e}"

        if payload.get("errors"):
            err_text = json.dumps(payload["errors"], ensure_ascii=False)
            last_error = f"GraphQL 请求失败: {err_text}"
            if _is_rate_limit_error(err_text) and attempt < max_retries - 1:
                _sleep_backoff(attempt, on_warning)
                continue
            return {}, last_error

        return (payload.get("data") or {}), ""

    return {}, last_error or "GraphQL 请求失败"


def _get_project_info(
    owner: str,
    project_number: int,
    *,
    on_warning: Optional[Callable[[str], None]] = None,
) -> tuple[Optional[dict[str, Any]], str]:
    """获取 ProjectV2 信息（含 Status 字段）。支持 user / organization。"""
    query = """
    query($owner: String!, $number: Int!) {
      user(login: $owner) {
        projectV2(number: $number) {
          id
          title
          fields(first: 50) {
            nodes {
              ... on ProjectV2SingleSelectField {
                id
                name
                options { id name }
              }
            }
          }
        }
      }
    }
    """

    data, err = gh_api_graphql(query, {"owner": owner, "number": project_number}, on_warning=on_warning)
    user = data.get("user") if data else None

    if not user:
        query_org = query.replace("user(login: $owner)", "organization(login: $owner)")
        data, err = gh_api_graphql(query_org, {"owner": owner, "number": project_number}, on_warning=on_warning)
        user = data.get("organization") if data else None

    if not user:
        return None, err or f"无法获取 owner={owner} 的 Project #{project_number}"

    project = user.get("projectV2") if isinstance(user, dict) else None
    if not project:
        return None, err or f"无法获取 Project #{project_number} 信息"

    status_field = None
    for field in (project.get("fields") or {}).get("nodes", []) or []:
        if not isinstance(field, dict):
            continue
        if str(field.get("name", "")).strip().lower() == "status":
            status_field = field
            break

    return {
        "id": project.get("id"),
        "title": project.get("title", ""),
        "status_field": status_field,
    }, ""


def _find_item_id_for_issue(
    project_id: str,
    issue_number: int,
    *,
    on_warning: Optional[Callable[[str], None]] = None,
    page_size: int = 100,
    max_pages: int = 20,
) -> tuple[Optional[str], str]:
    """在 Project items 中查找指定 Issue 对应的 item ID。未找到返回 (None, "")。"""
    query = """
    query($projectId: ID!, $after: String, $first: Int!) {
      node(id: $projectId) {
        ... on ProjectV2 {
          items(first: $first, after: $after) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id
              content { ... on Issue { number } }
            }
          }
        }
      }
    }
    """

    after: Optional[str] = None
    for _ in range(max_pages):
        variables: dict[str, Any] = {"projectId": project_id, "first": page_size}
        if after:
            variables["after"] = after
        data, err = gh_api_graphql(query, variables, on_warning=on_warning, timeout=45)
        if err:
            return None, err

        node = data.get("node") if isinstance(data, dict) else None
        items = (node or {}).get("items") if isinstance(node, dict) else None
        nodes = (items or {}).get("nodes") if isinstance(items, dict) else None
        if isinstance(nodes, list):
            for item in nodes:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if isinstance(content, dict) and content.get("number") == issue_number:
                    item_id = item.get("id")
                    return (str(item_id) if item_id else None), ""

        page_info = (items or {}).get("pageInfo") if isinstance(items, dict) else None
        if not isinstance(page_info, dict) or not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor")
        if not after:
            break

    return None, ""


def _update_item_single_select(
    project_id: str,
    item_id: str,
    field_id: str,
    option_id: str,
    *,
    on_warning: Optional[Callable[[str], None]] = None,
) -> tuple[bool, str]:
    mutation = """
    mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
      updateProjectV2ItemFieldValue(input: {
        projectId: $projectId,
        itemId: $itemId,
        fieldId: $fieldId,
        value: { singleSelectOptionId: $optionId }
      }) {
        projectV2Item { id }
      }
    }
    """

    data, err = gh_api_graphql(
        mutation,
        {
            "projectId": project_id,
            "itemId": item_id,
            "fieldId": field_id,
            "optionId": option_id,
        },
        on_warning=on_warning,
    )
    if err:
        return False, err
    updated = (data.get("updateProjectV2ItemFieldValue") or {}).get("projectV2Item") if isinstance(data, dict) else None
    return bool(updated), ""


def update_project_status(
    issue_number: int,
    new_status: ProjectStatus,
    project_number: int,
    owner: str,
) -> dict[str, Any]:
    """
    更新指定 Issue 在 Project 中的 Status。

    返回:
        {
          "ok": bool,
          "updated": bool,
          "warning"?: str,
          "error"?: str
        }
    """
    if new_status not in ALLOWED_STATUS:
        return {"ok": False, "updated": False, "error": f"无效 status: {new_status}（允许值: {sorted(ALLOWED_STATUS)}）"}

    warnings: list[str] = []
    warn = warnings.append

    project_info, err = _get_project_info(owner, project_number, on_warning=warn)
    if err or not project_info:
        return {"ok": False, "updated": False, "error": err or "无法获取 Project 信息"}

    project_id = project_info.get("id")
    if not project_id:
        return {"ok": False, "updated": False, "error": "Project ID 缺失（GraphQL 返回异常）"}

    status_field = project_info.get("status_field")
    if not isinstance(status_field, dict):
        return {
            "ok": True,
            "updated": False,
            "warning": f"Warning: Project #{project_number} 未找到 Status 字段，已跳过更新",
        }

    field_id = status_field.get("id")
    if not field_id:
        return {"ok": True, "updated": False, "warning": "Warning: Status 字段 ID 缺失，已跳过更新"}

    option_id_by_name = {
        str(opt.get("name", "")).strip().lower(): opt.get("id")
        for opt in (status_field.get("options") or [])
        if isinstance(opt, dict)
    }
    option_id = option_id_by_name.get(str(new_status).strip().lower())
    if not option_id:
        return {
            "ok": True,
            "updated": False,
            "warning": f"Warning: Status 字段缺少选项 '{new_status}'，已跳过更新",
        }

    item_id, err = _find_item_id_for_issue(str(project_id), issue_number, on_warning=warn)
    if err:
        return {"ok": False, "updated": False, "error": err}
    if not item_id:
        return {
            "ok": True,
            "updated": False,
            "warning": f"Warning: 未找到 Issue #{issue_number} 对应的 Project item，已跳过更新",
        }

    ok, err = _update_item_single_select(str(project_id), str(item_id), str(field_id), str(option_id), on_warning=warn)
    if err:
        return {"ok": False, "updated": False, "error": err}
    if not ok:
        return {"ok": False, "updated": False, "error": "更新 Status 失败（GraphQL 返回为空）"}

    result: dict[str, Any] = {
        "ok": True,
        "updated": True,
        "project_id": str(project_id),
        "item_id": str(item_id),
        "status": new_status,
    }
    if warnings:
        result["warnings"] = warnings
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="同步 Issue 状态到 GitHub Project（ProjectV2）")
    parser.add_argument("--project", type=int, required=True, help="Project 编号（必填）")
    parser.add_argument("--issue", type=int, required=True, help="Issue 编号（必填）")
    parser.add_argument("--status", required=True, help='目标状态（"In Progress" | "Done" | "Failed"）')
    parser.add_argument("--owner", help="Project 所属 owner（user/org），默认从当前仓库推断")
    parser.add_argument("--json", action="store_true", help="JSON 输出（用于调试）")
    args = parser.parse_args()

    owner = args.owner or get_repo_owner()
    if not owner:
        print("Error: 无法确定 owner，请使用 --owner 参数指定", file=sys.stderr)
        sys.exit(2)

    status = args.status.strip()
    if status not in ALLOWED_STATUS:
        allowed = ", ".join(sorted(ALLOWED_STATUS))
        print(f'Error: 无效 status="{args.status}"，允许值: {allowed}', file=sys.stderr)
        sys.exit(2)

    result = update_project_status(args.issue, status, args.project, owner)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result.get("updated"):
            print(f'✅ 已更新: owner={owner} project=#{args.project} issue=#{args.issue} status="{status}"')
        elif result.get("warning"):
            print(result["warning"], file=sys.stderr)
        else:
            print(f'Error: {result.get("error", "未知错误")}', file=sys.stderr)

    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()

