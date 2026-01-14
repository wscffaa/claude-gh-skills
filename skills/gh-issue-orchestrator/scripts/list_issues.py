#!/usr/bin/env python3
"""
GitHub Issue Orchestrator - Issue List & Analysis Script

获取 GitHub Issues，分析优先级和依赖关系，输出排序后的列表。
"""

import json
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Issue:
    number: int
    title: str
    priority: int  # 0=P0, 1=P1, 2=P2, 3=P3, 4=未标记
    labels: list[str] = field(default_factory=list)
    depends_on: list[int] = field(default_factory=list)
    assignee: Optional[str] = None
    milestone: Optional[str] = None
    is_epic: bool = False


def get_issues() -> list[dict]:
    """从 gh CLI 获取所有 open issues"""
    cmd = [
        "gh", "issue", "list",
        "--json", "number,title,labels,body,assignees,milestone",
        "--limit", "100"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    return json.loads(result.stdout)


def parse_priority(labels: list[dict]) -> int:
    """从 labels 解析优先级"""
    for label in labels:
        name = label.get("name", "")
        if name == "priority:p0":
            return 0
        if name == "priority:p1":
            return 1
        if name == "priority:p2":
            return 2
        if name == "priority:p3":
            return 3
    return 4  # 未标记


def parse_dependencies(body: str) -> list[int]:
    """从 body 解析依赖关系"""
    if not body:
        return []
    deps = []
    patterns = [
        r"[Dd]epends on #(\d+)",
        r"依赖 #(\d+)",
        r"[Bb]locked by #(\d+)",
        r"[Pp]art of #(\d+)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, body):
            deps.append(int(match.group(1)))
    return list(set(deps))


def is_epic(labels: list[dict]) -> bool:
    """检查是否是 Epic issue（只有 'epic' 标签，不是 'epic:xxx'）"""
    return any(label.get("name", "") == "epic" for label in labels)


def parse_issues(raw_issues: list[dict]) -> list[Issue]:
    """解析原始 issues 为 Issue 对象"""
    issues = []
    for raw in raw_issues:
        labels = raw.get("labels", [])
        assignees = raw.get("assignees", [])
        milestone = raw.get("milestone")

        issue = Issue(
            number=raw["number"],
            title=raw["title"],
            priority=parse_priority(labels),
            labels=[l.get("name", "") for l in labels],
            depends_on=parse_dependencies(raw.get("body", "")),
            assignee=assignees[0].get("login") if assignees else None,
            milestone=milestone.get("title") if milestone else None,
            is_epic=is_epic(labels),
        )
        issues.append(issue)
    return issues


def topological_sort(issues: list[Issue]) -> list[Issue]:
    """拓扑排序：无依赖的优先，同优先级按 number 排序"""
    issue_map = {i.number: i for i in issues}
    open_numbers = set(issue_map.keys())

    # 过滤掉已关闭的依赖
    for issue in issues:
        issue.depends_on = [d for d in issue.depends_on if d in open_numbers]

    # 按优先级和 number 排序
    def sort_key(i: Issue) -> tuple:
        has_open_deps = len(i.depends_on) > 0
        return (has_open_deps, i.priority, i.number)

    return sorted(issues, key=sort_key)


def format_list(issues: list[Issue]) -> str:
    """格式化输出列表模式"""
    priority_names = {0: "🔴 Critical (P0)", 1: "🟠 High (P1)",
                      2: "🟡 Medium (P2)", 3: "🟢 Low (P3)", 4: "⚪ Unset"}

    by_priority = defaultdict(list)
    for issue in issues:
        by_priority[issue.priority].append(issue)

    lines = [f"## Open Issues ({len(issues)} total)\n"]

    for p in sorted(by_priority.keys()):
        lines.append(f"\n### {priority_names[p]}")
        for issue in by_priority[p]:
            deps_str = ""
            if issue.depends_on:
                deps_str = f" ⚠️ 依赖 {', '.join(f'#{d}' for d in issue.depends_on)}"
            else:
                deps_str = " ✅ 可立即开始"
            assignee_str = f" @{issue.assignee}" if issue.assignee else ""
            epic_str = " [Epic]" if issue.is_epic else ""
            lines.append(f"- #{issue.number} {issue.title}{epic_str}{assignee_str}{deps_str}")

    return "\n".join(lines)


def format_next(issues: list[Issue]) -> str:
    """格式化输出推荐模式"""
    # 找到无阻塞依赖的最高优先级 issue
    candidates = [i for i in issues if not i.depends_on and not i.is_epic]
    if not candidates:
        return "没有找到可立即开始的 issue（所有 issues 都有未完成的依赖）"

    best = min(candidates, key=lambda i: (i.priority, i.number))
    priority_names = {0: "P0 Critical", 1: "P1 High", 2: "P2 Medium", 3: "P3 Low", 4: "Unset"}

    return f"""## 推荐下一个 Issue

**#{best.number}** {best.title}

**原因:**
- 优先级: {priority_names[best.priority]}
- 无阻塞依赖
- Milestone: {best.milestone or '未设置'}

**执行命令:**
```bash
claude -p "/gh-issue-implement {best.number}"
```
"""


def format_batch(issues: list[Issue], count: int) -> str:
    """格式化输出批量模式"""
    # 拓扑排序后取前 N 个
    sorted_issues = topological_sort(issues)
    # 过滤掉 Epic
    non_epic = [i for i in sorted_issues if not i.is_epic]
    batch = non_epic[:count]

    if not batch:
        return "没有找到可实现的 issues"

    lines = [f"## 批量实现计划 ({len(batch)} issues)\n"]
    lines.append("执行顺序 (按依赖拓扑排序):")

    numbers = []
    for idx, issue in enumerate(batch, 1):
        deps_str = f"依赖 {', '.join(f'#{d}' for d in issue.depends_on)}" if issue.depends_on else "无依赖"
        lines.append(f"{idx}. #{issue.number} {issue.title} - {deps_str}")
        numbers.append(str(issue.number))

    lines.append("\n**执行方式:**")
    lines.append("```bash")
    lines.append("# 串行执行 (确保依赖顺序)")
    lines.append(f"for issue in {' '.join(numbers)}; do")
    lines.append('  claude -p "/gh-issue-implement $issue"')
    lines.append("done")
    lines.append("```")

    return "\n".join(lines)


def format_auto(issues: list[Issue], count: Optional[int] = None) -> str:
    """输出 auto 模式的 JSON 数组（供 Claude 解析）"""
    # 拓扑排序
    sorted_issues = topological_sort(issues)
    # 过滤掉 Epic
    non_epic = [i for i in sorted_issues if not i.is_epic]
    # 只取无阻塞依赖的（可立即开始的）
    available = [i for i in non_epic if not i.depends_on]

    if count:
        available = available[:count]

    # 输出 JSON 数组
    return json.dumps([i.number for i in available])


def main():
    import argparse
    parser = argparse.ArgumentParser(description="GitHub Issue Orchestrator")
    parser.add_argument("--mode", choices=["list", "next", "batch", "auto"], default="list")
    parser.add_argument("--count", type=int, default=None, help="issue 数量限制")
    args = parser.parse_args()

    raw_issues = get_issues()
    issues = parse_issues(raw_issues)
    sorted_issues = topological_sort(issues)

    if args.mode == "list":
        print(format_list(sorted_issues))
    elif args.mode == "next":
        print(format_next(sorted_issues))
    elif args.mode == "batch":
        print(format_batch(issues, args.count or 3))
    elif args.mode == "auto":
        print(format_auto(issues, args.count))


if __name__ == "__main__":
    main()
