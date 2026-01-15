#!/usr/bin/env python3
"""
Generate PR Review summary report.

Usage:
    # From file
    python3 generate_report.py --input results.json --project-name "BasicOFR v1.0"

    # From stdin
    cat results.json | python3 generate_report.py --project-name "BasicOFR v1.0"

    # Markdown output (default)
    python3 generate_report.py --input results.json --format markdown

    # JSON output
    python3 generate_report.py --input results.json --format json

Input format (supports two structures):

Structure A (from batch_review.py unified):
    {
      "results": [
        {"issue": 108, "pr": 112, "status": "merged", "priority": "p0", "title": "...", "error": null}
      ],
      "summary": {"total": 2, "merged": 1, "failed": 0, "no_pr": 1}
    }

Structure B (separated no_pr):
    {
      "results": [
        {"issue": 108, "pr": 112, "title": "...", "priority": "p0", "status": "merged", "error": null},
        {"issue": 110, "pr": 114, "title": "...", "priority": "p1", "status": "failed", "error": "CI failed"}
      ],
      "no_pr": [
        {"issue": 111, "title": "...", "priority": "p1"}
      ]
    }

Output format (Markdown):
    ## Project PR Review Report

    Project: BasicOFR v1.0 Release (#1)
    Total Issues: 4 | With PR: 3 | Reviewed: 3

    | Issue | PR | Priority | Review | Merge | Status |
    |-------|-----|----------|--------|-------|--------|
    | #108 | #112 | P0 | Approved | Merged | Done |
    | #109 | - | P1 | - | - | No PR |

    ### Failed Reviews
    - #114: CI failed - test_xxx.py::test_case FAILED

    ### Next Steps
    - Fix #114: Run tests locally and fix failing test
    - Create PR for #111
"""

import argparse
import json
import sys
from typing import Any, Optional


def _read_json_input(path: Optional[str]) -> dict[str, Any]:
    """Read JSON input from file or stdin."""
    if path and path != "-":
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
        except OSError as e:
            print(f"Error: Failed to read input file: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        if sys.stdin.isatty():
            print("Error: No input provided. Use stdin pipe or --input", file=sys.stderr)
            sys.exit(1)
        raw = sys.stdin.read()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Error: JSON parse failed: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(data, dict):
        print("Error: Input JSON root must be an object", file=sys.stderr)
        sys.exit(1)
    return data


def _normalize_input(data: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Normalize input to unified list of items.

    Handles both Structure A (unified results) and Structure B (separate no_pr).
    """
    results = list(data.get("results", []))

    # Handle Structure B: merge no_pr items into results
    no_pr_items = data.get("no_pr", [])
    for item in no_pr_items:
        results.append({
            "issue": item.get("issue"),
            "pr": None,
            "title": item.get("title", ""),
            "priority": item.get("priority", ""),
            "status": "no_pr",
            "error": None,
        })

    return results


def _normalize_status(status: Optional[str]) -> str:
    """Normalize status string to canonical form."""
    if not status:
        return "unknown"
    s = str(status).lower().strip()
    status_map = {
        "merged": "merged",
        "approved": "approved",
        "changes_requested": "changes_requested",
        "pending": "pending",
        "no_pr": "no_pr",
        "failed": "failed",
        "error": "error",
        "skipped": "skipped",
        "done": "merged",
        "in_progress": "pending",
    }
    return status_map.get(s, s)


def _get_status_display(status: str, use_emoji: bool = True) -> tuple[str, str, str]:
    """
    Return (Review, Merge, Status) display values for a given status.

    Args:
        status: Normalized status string
        use_emoji: Whether to use emoji markers

    Returns:
        Tuple of (review_col, merge_col, status_col)
    """
    status = _normalize_status(status)

    # Emoji markers
    check = "\u2705" if use_emoji else ""  # Green checkmark
    cross = "\u274c" if use_emoji else ""  # Red X

    if status == "merged":
        review = f"{check} Approved" if use_emoji else "Approved"
        merge = f"{check} Merged" if use_emoji else "Merged"
        return (review, merge, "Done")
    elif status == "approved":
        review = f"{check} Approved" if use_emoji else "Approved"
        return (review, "Pending", "Ready")
    elif status == "changes_requested":
        review = f"{cross} Changes" if use_emoji else "Changes"
        return (review, "-", "Needs Work")
    elif status == "pending":
        return ("Pending", "-", "In Review")
    elif status == "no_pr":
        return ("-", "-", "No PR")
    elif status in ("failed", "error"):
        review = f"{cross} Failed" if use_emoji else "Failed"
        return (review, "-", "In Progress")
    elif status == "skipped":
        return ("-", "-", "Skipped")
    else:
        return ("-", "-", status.title() if status else "Unknown")


def _compute_summary(results: list[dict[str, Any]], data: dict[str, Any]) -> dict[str, int]:
    """Compute summary statistics from results."""
    # Use provided summary if available
    if "summary" in data:
        return data["summary"]

    # Otherwise compute from results
    total = len(results)
    merged = sum(1 for r in results if _normalize_status(r.get("status")) == "merged")
    approved = sum(1 for r in results if _normalize_status(r.get("status")) == "approved")
    failed = sum(1 for r in results if _normalize_status(r.get("status")) in ("failed", "error"))
    no_pr = sum(1 for r in results if _normalize_status(r.get("status")) == "no_pr")
    changes = sum(1 for r in results if _normalize_status(r.get("status")) == "changes_requested")

    return {
        "total": total,
        "merged": merged,
        "approved": approved,
        "failed": failed,
        "no_pr": no_pr,
        "changes_requested": changes,
    }


def _sort_by_priority(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort results by priority (P0 > P1 > P2 > P3 > None), then by issue number."""
    priority_rank = {"p0": 0, "p1": 1, "p2": 2, "p3": 3}

    def sort_key(item: dict) -> tuple[int, int]:
        p = item.get("priority", "")
        p_lower = p.lower() if p else ""
        rank = priority_rank.get(p_lower, 99)
        issue = item.get("issue", 0) or 0
        return (rank, issue)

    return sorted(results, key=sort_key)


def _generate_markdown_report(
    data: dict[str, Any],
    project_name: str,
) -> str:
    """Generate Markdown format report."""
    results = _normalize_input(data)
    results = _sort_by_priority(results)
    summary = _compute_summary(results, data)

    total = summary.get("total", len(results))
    merged = summary.get("merged", 0)
    no_pr = summary.get("no_pr", 0)
    with_pr = total - no_pr
    reviewed = with_pr  # All items with PR are considered reviewed

    lines: list[str] = []

    # Header
    lines.append("## Project PR Review Report")
    lines.append("")

    # Project info
    lines.append(f"Project: {project_name}")
    lines.append(f"Total Issues: {total} | With PR: {with_pr} | Reviewed: {reviewed}")
    lines.append("")

    # Table
    if results:
        lines.append("| Issue | PR | Priority | Review | Merge | Status |")
        lines.append("|-------|-----|----------|--------|-------|--------|")

        for item in results:
            issue_num = item.get("issue")
            pr_num = item.get("pr")
            priority = item.get("priority", "")
            status = item.get("status", "")

            # Format columns
            issue_col = f"#{issue_num}" if issue_num else "-"
            pr_col = f"#{pr_num}" if pr_num else "-"
            priority_col = priority.upper() if priority else "-"

            review_col, merge_col, status_col = _get_status_display(status, use_emoji=True)

            lines.append(f"| {issue_col} | {pr_col} | {priority_col} | {review_col} | {merge_col} | {status_col} |")

        lines.append("")

    # Failed Reviews section
    failed_items = [
        r for r in results
        if _normalize_status(r.get("status")) in ("failed", "error", "changes_requested")
    ]

    lines.append("### Failed Reviews")
    if failed_items:
        for item in failed_items:
            pr_num = item.get("pr")
            error = item.get("error") or "Unknown error"

            if pr_num:
                lines.append(f"- #{pr_num}: {error}")
            else:
                issue_num = item.get("issue", "?")
                lines.append(f"- Issue #{issue_num}: {error}")
    else:
        lines.append("- (none)")
    lines.append("")

    # Next Steps section
    lines.append("### Next Steps")
    next_steps: list[str] = []

    # Failed items need fixing
    failed_fix_items = [r for r in results if _normalize_status(r.get("status")) in ("failed", "error")]
    for item in failed_fix_items:
        pr_num = item.get("pr")
        error = item.get("error") or "Unknown error"
        if pr_num:
            # Generate actionable suggestion based on error
            suggestion = _get_fix_suggestion(error)
            next_steps.append(f"Fix #{pr_num}: {suggestion}")

    # Items needing PR creation
    no_pr_items = [r for r in results if _normalize_status(r.get("status")) == "no_pr"]
    if no_pr_items:
        issue_refs = ", ".join(f"#{r.get('issue')}" for r in no_pr_items if r.get('issue'))
        if issue_refs:
            next_steps.append(f"Create PR for {issue_refs}")

    # Items with requested changes
    changes_items = [r for r in results if _normalize_status(r.get("status")) == "changes_requested"]
    if changes_items:
        for item in changes_items:
            pr_num = item.get("pr")
            if pr_num:
                next_steps.append(f"Address review comments on #{pr_num}")

    # Approved items waiting for merge
    approved_items = [r for r in results if _normalize_status(r.get("status")) == "approved"]
    if approved_items:
        pr_refs = ", ".join(f"#{r.get('pr')}" for r in approved_items if r.get("pr"))
        if pr_refs:
            next_steps.append(f"Merge approved PRs: {pr_refs}")

    if next_steps:
        for step in next_steps:
            lines.append(f"- {step}")
    else:
        lines.append("- All tasks completed!")

    return "\n".join(lines)


def _get_fix_suggestion(error: str) -> str:
    """Generate fix suggestion based on error message."""
    error_lower = error.lower()

    if "ci failed" in error_lower or "test" in error_lower:
        return "Run tests locally and fix failing test"
    elif "lint" in error_lower or "format" in error_lower:
        return "Fix linting/formatting issues"
    elif "conflict" in error_lower:
        return "Resolve merge conflicts"
    elif "review" in error_lower:
        return "Address reviewer feedback"
    elif "build" in error_lower:
        return "Fix build errors"
    else:
        return "Investigate and resolve the issue"


def _generate_json_report(
    data: dict[str, Any],
    project_name: str,
) -> str:
    """Generate JSON format report."""
    results = _normalize_input(data)
    results = _sort_by_priority(results)
    summary = _compute_summary(results, data)

    # Enhance each result with display info
    enhanced_results = []
    for item in results:
        status = _normalize_status(item.get("status"))
        review_col, merge_col, status_col = _get_status_display(status, use_emoji=False)

        enhanced = {
            "issue": item.get("issue"),
            "pr": item.get("pr"),
            "priority": item.get("priority"),
            "title": item.get("title"),
            "status": status,
            "display": {
                "review": review_col,
                "merge": merge_col,
                "status": status_col,
            },
        }

        if item.get("error"):
            enhanced["error"] = item.get("error")

        enhanced_results.append(enhanced)

    # Compute next steps
    next_steps: list[dict[str, Any]] = []

    # Failed items
    failed_items = [r for r in results if _normalize_status(r.get("status")) in ("failed", "error")]
    if failed_items:
        next_steps.append({
            "action": "fix_failed",
            "items": [
                {"pr": r.get("pr"), "issue": r.get("issue"), "error": r.get("error")}
                for r in failed_items
            ],
        })

    # No PR items
    no_pr_items = [r for r in results if _normalize_status(r.get("status")) == "no_pr"]
    if no_pr_items:
        next_steps.append({
            "action": "create_pr",
            "issues": [r.get("issue") for r in no_pr_items if r.get("issue")],
        })

    # Changes requested
    changes_items = [r for r in results if _normalize_status(r.get("status")) == "changes_requested"]
    if changes_items:
        next_steps.append({
            "action": "address_comments",
            "prs": [r.get("pr") for r in changes_items if r.get("pr")],
        })

    # Approved items
    approved_items = [r for r in results if _normalize_status(r.get("status")) == "approved"]
    if approved_items:
        next_steps.append({
            "action": "merge_pr",
            "prs": [r.get("pr") for r in approved_items if r.get("pr")],
        })

    output = {
        "project_name": project_name,
        "summary": summary,
        "results": enhanced_results,
        "next_steps": next_steps,
    }

    return json.dumps(output, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Generate PR Review summary report")
    parser.add_argument(
        "--input",
        help="Input JSON file from batch_review.py (default: stdin)",
    )
    parser.add_argument(
        "--project-name",
        default="Project",
        help="Project name for report title",
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "json"],
        default="markdown",
        help="Output format: markdown (default) or json",
    )
    args = parser.parse_args()

    data = _read_json_input(args.input)

    if args.format == "json":
        output = _generate_json_report(data, args.project_name)
    else:
        output = _generate_markdown_report(data, args.project_name)

    print(output)


if __name__ == "__main__":
    main()
