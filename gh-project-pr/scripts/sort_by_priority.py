#!/usr/bin/env python3
"""
按优先级排序 Issue-PR 映射列表。

用法:
    # 从 stdin 读取
    cat mappings.json | python3 sort_by_priority.py

    # 从文件读取
    python3 sort_by_priority.py --input mappings.json

    # 仅保留指定优先级
    python3 sort_by_priority.py --input mappings.json --priority p0,p1

    # JSON 输出（默认）
    python3 sort_by_priority.py --input mappings.json --json

功能:
    - 按 P0 -> P1 -> P2 -> P3 -> 无优先级 排序
    - 同优先级内按 Issue 编号升序
    - 过滤已合并的 PR（state == "merged"）
    - 支持 --priority 参数过滤指定优先级

输入格式 (from get_project_prs.py):
    {
      "mappings": [
        {"issue": 108, "pr": 112, "state": "open", "priority": "p0"},
        {"issue": 109, "pr": 113, "state": "merged", "priority": "p1"}
      ]
    }

输出格式:
    {
      "sorted": [
        {"issue": 108, "pr": 112, "state": "open", "priority": "p0"}
      ],
      "filtered_count": 1,
      "total_count": 2
    }
"""

import argparse
import json
import sys
from typing import Optional


# Priority rank: p0=0, p1=1, p2=2, p3=3, None=4
PRIORITY_RANK = {"p0": 0, "p1": 1, "p2": 2, "p3": 3, None: 4}


def get_priority_rank(priority: Optional[str]) -> int:
    """Get numeric rank for priority. Lower is higher priority."""
    if priority is None:
        return PRIORITY_RANK[None]
    return PRIORITY_RANK.get(priority.lower(), PRIORITY_RANK[None])


def sort_key(mapping: dict) -> tuple[int, int]:
    """
    Sort key: (priority_rank, issue_number).

    Lower priority_rank = higher priority.
    Same priority -> sort by issue number ascending.
    """
    priority = mapping.get("priority")
    issue = mapping.get("issue", 0)
    return (get_priority_rank(priority), issue)


def filter_merged(mappings: list[dict]) -> list[dict]:
    """Filter out merged PRs (state == 'merged')."""
    return [m for m in mappings if m.get("state") != "merged"]


def filter_by_priority(
    mappings: list[dict], priorities: Optional[set[str]]
) -> list[dict]:
    """Filter mappings to only include specified priorities."""
    if priorities is None:
        return mappings

    def matches(m: dict) -> bool:
        p = m.get("priority")
        if p is None:
            return "none" in priorities or "" in priorities
        return p.lower() in priorities

    return [m for m in mappings if matches(m)]


def parse_priority_arg(arg: Optional[str]) -> Optional[set[str]]:
    """Parse --priority argument into a set of priorities."""
    if arg is None:
        return None
    priorities = set()
    for p in arg.split(","):
        p = p.strip().lower()
        if p:
            priorities.add(p)
    return priorities if priorities else None


def read_input(input_file: Optional[str]) -> dict:
    """Read input from file or stdin."""
    if input_file:
        try:
            with open(input_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            print(f"Error: 文件不存在: {input_file}", file=sys.stderr)
            sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"Error: JSON 解析失败: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        # Read from stdin
        try:
            return json.load(sys.stdin)
        except json.JSONDecodeError as e:
            print(f"Error: stdin JSON 解析失败: {e}", file=sys.stderr)
            sys.exit(1)


def format_text_output(result: dict) -> str:
    """Format result as human-readable text."""
    lines = []
    lines.append("=" * 60)
    lines.append("Issue-PR 排序结果（按优先级）")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"统计:")
    lines.append(f"  原始条目数: {result['total_count']}")
    lines.append(f"  过滤后条目数: {result['filtered_count']}")
    lines.append("")
    lines.append("排序列表:")
    lines.append("-" * 60)

    for m in result["sorted"]:
        prio_str = f"[{m.get('priority', '--') or '--'}]"
        pr_str = f"PR #{m['pr']:>4}" if m.get("pr") else "(no PR) "
        state_icon = {"open": "O", "closed": "X", None: "-"}.get(m.get("state"), "?")
        title = (m.get("title") or "")[:40]
        lines.append(
            f"  Issue #{m['issue']:>4} -> {pr_str} ({state_icon}) {prio_str:>6} {title}"
        )

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="按优先级排序 Issue-PR 映射",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  cat mappings.json | python3 sort_by_priority.py
  python3 sort_by_priority.py --input mappings.json
  python3 sort_by_priority.py --input mappings.json --priority p0,p1
  python3 sort_by_priority.py --input mappings.json --json
""",
    )
    parser.add_argument("--input", "-i", dest="input_file", help="输入文件路径（默认从 stdin 读取）")
    parser.add_argument(
        "--priority",
        "-p",
        help="仅保留指定优先级，逗号分隔（如 p0,p1）。使用 'none' 匹配无优先级项",
    )
    parser.add_argument(
        "--json", "-j", action="store_true", help="JSON 格式输出（默认为文本格式）"
    )
    parser.add_argument(
        "--include-merged", action="store_true", help="包含已合并的 PR（默认过滤掉）"
    )
    args = parser.parse_args()

    # Read input
    data = read_input(args.input_file)

    # Extract mappings
    mappings = data.get("mappings", [])
    if not isinstance(mappings, list):
        print("Error: 输入格式错误，mappings 应为数组", file=sys.stderr)
        sys.exit(1)

    total_count = len(mappings)

    # Filter merged PRs (unless --include-merged)
    if not args.include_merged:
        mappings = filter_merged(mappings)

    # Filter by priority if specified
    priority_filter = parse_priority_arg(args.priority)
    if priority_filter:
        mappings = filter_by_priority(mappings, priority_filter)

    # Sort by priority, then by issue number
    sorted_mappings = sorted(mappings, key=sort_key)

    # Build result
    result = {
        "sorted": sorted_mappings,
        "filtered_count": len(sorted_mappings),
        "total_count": total_count,
    }

    # Output
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_text_output(result))


if __name__ == "__main__":
    main()
