#!/usr/bin/env python3
"""
批量或单个更新 GitHub Project Item 状态。

用法:
    # 更新单个 Issue
    python3 update_status.py --project 1 --issue 108 --status Done

    # 批量更新（从 batch_review.py 输出）
    python3 update_status.py --project 1 --input results.json

    # 用户级 Project
    python3 update_status.py --project 1 --issue 108 --status Done --user

    # 指定 owner
    python3 update_status.py --project 1 --issue 108 --status Done --owner wscffaa

输入格式 (--input):
    {
      "results": [
        {"issue": 108, "pr": 112, "status": "merged"},
        {"issue": 109, "pr": 113, "status": "approved"}
      ]
    }

状态映射 (PR status -> Project status):
    merged -> Done
    其他 -> 保持不变（跳过）
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Literal, Optional

# 允许的 Project Status 值
ProjectStatus = Literal["Todo", "In Progress", "Done", "Failed"]
ALLOWED_STATUS: set[str] = {"Todo", "In Progress", "Done", "Failed"}

# PR 状态到 Project 状态的映射
PR_STATUS_TO_PROJECT: dict[str, str] = {
    "merged": "Done",
    # 其他状态不自动映射
}


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
    delay = 5 * (2**attempt)  # 5s, 10s, 20s
    if on_warning:
        on_warning(f"Rate limit hit, retrying in {delay}s ({attempt + 1}/3)")
    time.sleep(delay)


def gh_api_graphql(
    query: str,
    variables: dict[str, Any],
    *,
    timeout: int = 30,
    max_retries: int = 3,
    on_warning: Optional[Callable[[str], None]] = None,
) -> tuple[dict[str, Any], str]:
    """调用 gh api graphql，返回 (data, error)。遇到 rate limit 自动退避重试。"""
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    for k, v in variables.items():
        cmd.extend(["-F", f"{k}={v}"])

    last_error = ""
    for attempt in range(max_retries):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            last_error = "gh api graphql timed out"
            break
        except FileNotFoundError:
            last_error = "gh not found, please install and auth: gh auth login"
            break
        except Exception as e:
            last_error = f"gh api graphql failed: {e}"
            break

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()

        if result.returncode != 0:
            last_error = stderr or stdout or "gh api graphql failed"
            if _is_rate_limit_error(last_error) and attempt < max_retries - 1:
                _sleep_backoff(attempt, on_warning)
                continue
            return {}, last_error

        try:
            payload = json.loads(stdout) if stdout else {}
        except json.JSONDecodeError as e:
            return {}, f"GraphQL JSON parse error: {e}"

        if payload.get("errors"):
            err_text = json.dumps(payload["errors"], ensure_ascii=False)
            last_error = f"GraphQL error: {err_text}"
            if _is_rate_limit_error(err_text) and attempt < max_retries - 1:
                _sleep_backoff(attempt, on_warning)
                continue
            return {}, last_error

        return (payload.get("data") or {}), ""

    return {}, last_error or "GraphQL request failed"


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
        # Fallback: try organization
        query_org = query.replace("user(login: $owner)", "organization(login: $owner)")
        data, err = gh_api_graphql(query_org, {"owner": owner, "number": project_number}, on_warning=on_warning)
        user = data.get("organization") if data else None

    if not user:
        return None, err or f"Cannot get Project #{project_number} for owner={owner}"

    project = user.get("projectV2") if isinstance(user, dict) else None
    if not project:
        return None, err or f"Cannot get Project #{project_number} info"

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
    """在 Project items 中查找指定 Issue 对应的 item ID。"""
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
    """更新 Project Item 的 SingleSelect 字段。"""
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


def update_single_issue(
    issue_number: int,
    new_status: str,
    project_info: dict[str, Any],
    *,
    on_warning: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """
    更新单个 Issue 在 Project 中的 Status。

    返回:
        {
          "ok": bool,
          "updated": bool,
          "issue": int,
          "status": str,
          "warning"?: str,
          "error"?: str
        }
    """
    project_id = project_info.get("id")
    status_field = project_info.get("status_field")

    if not project_id:
        return {
            "ok": False,
            "updated": False,
            "issue": issue_number,
            "status": new_status,
            "error": "Project ID missing",
        }

    if not isinstance(status_field, dict):
        return {
            "ok": True,
            "updated": False,
            "issue": issue_number,
            "status": new_status,
            "warning": "Status field not found in Project, skipped",
        }

    field_id = status_field.get("id")
    if not field_id:
        return {
            "ok": True,
            "updated": False,
            "issue": issue_number,
            "status": new_status,
            "warning": "Status field ID missing, skipped",
        }

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
            "issue": issue_number,
            "status": new_status,
            "warning": f"Status option '{new_status}' not found, skipped",
        }

    item_id, err = _find_item_id_for_issue(str(project_id), issue_number, on_warning=on_warning)
    if err:
        return {
            "ok": False,
            "updated": False,
            "issue": issue_number,
            "status": new_status,
            "error": err,
        }
    if not item_id:
        return {
            "ok": True,
            "updated": False,
            "issue": issue_number,
            "status": new_status,
            "warning": f"Issue #{issue_number} not found in Project, skipped",
        }

    ok, err = _update_item_single_select(
        str(project_id), str(item_id), str(field_id), str(option_id), on_warning=on_warning
    )
    if err:
        return {
            "ok": False,
            "updated": False,
            "issue": issue_number,
            "status": new_status,
            "error": err,
        }
    if not ok:
        return {
            "ok": False,
            "updated": False,
            "issue": issue_number,
            "status": new_status,
            "error": "Update failed (GraphQL returned empty)",
        }

    return {
        "ok": True,
        "updated": True,
        "issue": issue_number,
        "status": new_status,
        "item_id": str(item_id),
    }


def load_batch_input(input_path: str) -> tuple[list[dict[str, Any]], str]:
    """
    从 JSON 文件加载批量输入。

    期望格式:
        {
          "results": [
            {"issue": 108, "pr": 112, "status": "merged"},
            ...
          ]
        }

    返回 (items, error)
    """
    path = Path(input_path)
    if not path.exists():
        return [], f"Input file not found: {input_path}"

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return [], f"JSON parse error: {e}"
    except Exception as e:
        return [], f"Failed to read file: {e}"

    if not isinstance(data, dict):
        return [], "Invalid format: expected JSON object"

    results = data.get("results")
    if not isinstance(results, list):
        return [], "Invalid format: 'results' must be an array"

    return results, ""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update GitHub Project Item status (single or batch)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Update single issue
  python3 update_status.py --project 1 --issue 108 --status Done

  # Batch update from file
  python3 update_status.py --project 1 --input results.json

  # User-level project
  python3 update_status.py --project 1 --issue 108 --status Done --user
""",
    )
    parser.add_argument("--project", type=int, required=True, help="Project number (required)")
    parser.add_argument("--issue", type=int, help="Issue number (single mode)")
    parser.add_argument("--status", help="Target status (single mode): Todo, In Progress, Done, Failed")
    parser.add_argument("--input", dest="input_file", help="Input JSON file (batch mode)")
    parser.add_argument("--owner", help="Project owner (user/org), auto-detected from repo if omitted")
    parser.add_argument(
        "--user",
        action="store_true",
        help="User-level project (same behavior, for compatibility)",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    # Validate arguments
    if args.issue is None and args.input_file is None:
        print("Error: must provide --issue or --input", file=sys.stderr)
        sys.exit(2)

    if args.issue is not None and args.input_file is not None:
        print("Error: cannot use --issue and --input together", file=sys.stderr)
        sys.exit(2)

    if args.issue is not None and args.status is None:
        print("Error: --status is required when using --issue", file=sys.stderr)
        sys.exit(2)

    if args.status is not None and args.status not in ALLOWED_STATUS:
        allowed = ", ".join(sorted(ALLOWED_STATUS))
        print(f'Error: invalid status="{args.status}", allowed: {allowed}', file=sys.stderr)
        sys.exit(2)

    # Determine owner
    owner = args.owner or get_repo_owner()
    if not owner:
        print("Error: cannot determine owner, use --owner", file=sys.stderr)
        sys.exit(2)

    # Get project info (shared for all updates)
    warnings: list[str] = []
    warn = warnings.append

    if not args.json:
        print(f"Fetching Project #{args.project} info...", file=sys.stderr)

    project_info, err = _get_project_info(owner, args.project, on_warning=warn)
    if err or not project_info:
        print(f"Error: {err or 'Cannot get Project info'}", file=sys.stderr)
        sys.exit(1)

    # Single mode
    if args.issue is not None:
        result = update_single_issue(args.issue, args.status, project_info, on_warning=warn)

        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            if result.get("updated"):
                print(f'Updated: Issue #{args.issue} -> Status="{args.status}"')
            elif result.get("warning"):
                print(f"Warning: {result['warning']}", file=sys.stderr)
            else:
                print(f"Error: {result.get('error', 'Unknown error')}", file=sys.stderr)

        sys.exit(0 if result.get("ok") else 1)

    # Batch mode
    items, err = load_batch_input(args.input_file)
    if err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(2)

    if not items:
        print("Warning: no items to process", file=sys.stderr)
        if args.json:
            print(json.dumps({"results": [], "stats": {"total": 0, "updated": 0, "skipped": 0, "failed": 0}}))
        sys.exit(0)

    # Process batch
    results: list[dict[str, Any]] = []
    stats = {"total": len(items), "updated": 0, "skipped": 0, "failed": 0}

    for i, item in enumerate(items, 1):
        issue_number = item.get("issue")
        pr_status = str(item.get("status", "")).lower()

        if not isinstance(issue_number, int):
            if not args.json:
                print(f"[{i}/{len(items)}] Skipped: invalid issue number", file=sys.stderr)
            stats["skipped"] += 1
            continue

        # Map PR status to Project status
        project_status = PR_STATUS_TO_PROJECT.get(pr_status)
        if not project_status:
            if not args.json:
                print(f"[{i}/{len(items)}] Issue #{issue_number}: skipped (status={pr_status})", file=sys.stderr)
            stats["skipped"] += 1
            results.append({
                "ok": True,
                "updated": False,
                "issue": issue_number,
                "pr_status": pr_status,
                "reason": f"PR status '{pr_status}' does not map to Project status",
            })
            continue

        if not args.json:
            print(f"[{i}/{len(items)}] Updating Issue #{issue_number} -> {project_status}...", file=sys.stderr)

        result = update_single_issue(issue_number, project_status, project_info, on_warning=warn)
        results.append(result)

        if result.get("updated"):
            stats["updated"] += 1
        elif result.get("ok"):
            stats["skipped"] += 1
        else:
            stats["failed"] += 1

    # Output
    output = {"results": results, "stats": stats}

    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'='*50}", file=sys.stderr)
        print(f"Summary: total={stats['total']} updated={stats['updated']} skipped={stats['skipped']} failed={stats['failed']}", file=sys.stderr)

    sys.exit(0 if stats["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
