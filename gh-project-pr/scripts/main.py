#!/usr/bin/env python3
"""
gh-project-pr 主入口。

用法:
    # 预览模式（只执行 Phase 1-3，不执行审查/更新/报告）
    python3 main.py --project 1 --dry-run

    # 完整执行
    python3 main.py --project 1

    # 指定 owner
    python3 main.py --project 1 --owner wscffaa --dry-run

    # JSON 输出
    python3 main.py --project 1 --dry-run --json

Pipeline Phases:
    Phase 1: 获取 Project Items
    Phase 2: 查找关联 PR
    Phase 3: 按优先级排序
    ------- dry-run stops here -------
    Phase 4: 批量审查 PR
    Phase 5: 更新 Project 状态
    Phase 6: 生成报告
"""

import argparse
import json
import sys
from typing import Optional

# Import pipeline modules
from get_project_prs import (
    get_repo_owner,
    list_project_items,
    filter_project_issues,
    find_pr_for_issue,
    get_pr_state,
)
from sort_by_priority import (
    filter_merged,
    filter_by_priority,
    parse_priority_arg,
    sort_key,
)


def run_phase_1_2(
    owner: str,
    project_number: int,
    verbose: bool = False,
) -> tuple[list[dict], dict]:
    """
    Phase 1-2: 获取 Project Items 并查找关联 PR。

    Returns:
        (mappings, stats)
    """
    if verbose:
        print(f"[Phase 1] Fetching Project #{project_number} items...", file=sys.stderr)

    items = list_project_items(owner, project_number)
    issues = filter_project_issues(items)

    if verbose:
        print(f"  Found {len(issues)} non-Done issues", file=sys.stderr)
        print(f"[Phase 2] Finding PRs for each issue...", file=sys.stderr)

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
        if verbose:
            print(
                f"  [{i}/{len(issues)}] Issue #{issue['number']}...",
                file=sys.stderr,
                end="",
                flush=True,
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

            if verbose:
                print(f" -> PR #{pr['number']} ({state})", file=sys.stderr)
        else:
            stats["without_pr"] += 1
            if verbose:
                print(" -> (no PR)", file=sys.stderr)

        mappings.append(mapping)

    return mappings, stats


def run_phase_3(
    mappings: list[dict],
    priority_filter: Optional[set[str]] = None,
    include_merged: bool = False,
    verbose: bool = False,
) -> list[dict]:
    """
    Phase 3: 排序并过滤。

    Returns:
        sorted_mappings
    """
    if verbose:
        print("[Phase 3] Sorting by priority...", file=sys.stderr)

    # Filter merged PRs (unless include_merged)
    if not include_merged:
        mappings = filter_merged(mappings)

    # Filter by priority if specified
    if priority_filter:
        mappings = filter_by_priority(mappings, priority_filter)

    # Sort by priority, then by issue number
    sorted_mappings = sorted(mappings, key=sort_key)

    if verbose:
        print(f"  {len(sorted_mappings)} items after filtering", file=sys.stderr)

    return sorted_mappings


def format_dry_run_table(sorted_mappings: list[dict], stats: dict) -> str:
    """Format dry-run output as markdown table."""
    lines = []

    # Header
    total = stats.get("total_issues", len(sorted_mappings))
    with_pr = stats.get("with_pr", 0)
    without_pr = stats.get("without_pr", 0)

    lines.append(f"Found {len(sorted_mappings)} PRs to review:")
    lines.append("")

    # Table
    lines.append("| Issue | PR | Priority | Title |")
    lines.append("|-------|-----|----------|-------|")

    for m in sorted_mappings:
        issue_col = f"#{m['issue']}"
        pr_col = f"#{m['pr']}" if m.get("pr") else "-"
        priority = m.get("priority")
        priority_col = priority.upper() if priority else "-"
        title = (m.get("title") or "")[:40]
        if not m.get("pr"):
            title = "(no PR)"

        lines.append(f"| {issue_col} | {pr_col} | {priority_col} | {title} |")

    lines.append("")
    lines.append("Run without --dry-run to execute review.")

    return "\n".join(lines)


def format_dry_run_json(sorted_mappings: list[dict], stats: dict) -> str:
    """Format dry-run output as JSON."""
    output = {
        "dry_run": True,
        "to_review": sorted_mappings,
        "stats": stats,
    }
    return json.dumps(output, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description="gh-project-pr: GitHub Project PR Review Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Preview mode (Phase 1-3 only)
  python3 main.py --project 1 --dry-run

  # Full execution
  python3 main.py --project 1

  # Specify owner
  python3 main.py --project 1 --owner wscffaa --dry-run

  # JSON output
  python3 main.py --project 1 --dry-run --json

  # Filter by priority
  python3 main.py --project 1 --dry-run --priority p0,p1
""",
    )
    parser.add_argument(
        "--project",
        type=int,
        required=True,
        help="Project number (required)",
    )
    parser.add_argument(
        "--owner",
        help="Project owner (user/org), auto-detected from repo if omitted",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview mode: execute Phase 1-3 only, show PRs to review",
    )
    parser.add_argument(
        "--priority",
        help="Filter by priority (comma-separated, e.g., p0,p1)",
    )
    parser.add_argument(
        "--include-merged",
        action="store_true",
        help="Include merged PRs in output (default: filter out)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="JSON output format",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose output to stderr",
    )

    args = parser.parse_args()

    # Determine owner
    owner = args.owner or get_repo_owner()
    if not owner:
        print("Error: Cannot determine owner, use --owner", file=sys.stderr)
        sys.exit(1)

    # Phase 1-2: Get mappings
    mappings, stats = run_phase_1_2(
        owner=owner,
        project_number=args.project,
        verbose=args.verbose,
    )

    # Phase 3: Sort and filter
    priority_filter = parse_priority_arg(args.priority)
    sorted_mappings = run_phase_3(
        mappings=mappings,
        priority_filter=priority_filter,
        include_merged=args.include_merged,
        verbose=args.verbose,
    )

    # Dry-run mode: output preview and exit
    if args.dry_run:
        if args.json:
            print(format_dry_run_json(sorted_mappings, stats))
        else:
            print(format_dry_run_table(sorted_mappings, stats))
        sys.exit(0)

    # Full execution: Phase 4-6
    # TODO: Implement full pipeline execution
    # For now, just output the sorted mappings
    print("Full execution mode not yet implemented.", file=sys.stderr)
    print("Use individual scripts for Phase 4-6:", file=sys.stderr)
    print("  - batch_review.py", file=sys.stderr)
    print("  - update_status.py", file=sys.stderr)
    print("  - generate_report.py", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
