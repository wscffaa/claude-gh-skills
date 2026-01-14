#!/usr/bin/env python3
"""
根据 get_project_issues.py --json 的输出，把 Issues 按优先级分批并在批内按依赖关系拓扑排序。

需求要点:
- 从 stdin 或 --input 文件读取 JSON
- priority 缺失/无效时默认归为 p2
- 通过 gh issue view 获取 body，并解析依赖关系:
    r'(?:Depends on|依赖|Blocked by|Part of)\\s*#(\\d+)'
- 按优先级分批顺序: p0 → p1 → p2 → p3
- 每批内按依赖关系拓扑排序（被依赖的 issue 优先）
- 循环依赖: 输出警告并回退到按 issue number 排序
- 跨批次依赖（如 P1 依赖 P2）: 输出警告

输出:
    默认: 可读的批次列表 + 警告
    --json: JSON 输出:
        {
          "batches": [
            {"priority": "p0", "issues": [42, 43]},
            {"priority": "p1", "issues": [44, 45, 46]}
          ],
          "warnings": ["跨批次依赖: #45 (P1) 依赖 #50 (P2)"]
        }
"""

import argparse
import heapq
import json
import re
import subprocess
import sys
from typing import Any, Optional


PRIORITY_ORDER = ["p0", "p1", "p2", "p3"]
PRIORITY_RANK = {p: i for i, p in enumerate(PRIORITY_ORDER)}
DEP_RE = re.compile(r"(?:Depends on|依赖|Blocked by|Part of)\s*#(\d+)", re.IGNORECASE)


def _read_json_input(path: Optional[str]) -> dict[str, Any]:
    if path and path != "-":
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
        except OSError as e:
            print(f"Error: 读取输入文件失败: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        if sys.stdin.isatty():
            print("Error: 未提供输入，请通过 stdin 管道或 --input 指定 JSON 文件", file=sys.stderr)
            sys.exit(1)
        raw = sys.stdin.read()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Error: JSON 解析失败: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(data, dict):
        print("Error: 输入 JSON 顶层必须为对象（包含 issues 字段）", file=sys.stderr)
        sys.exit(1)
    return data


def _normalize_priority(value: Any, warnings: list[str], issue_number: int) -> str:
    if isinstance(value, str):
        p = value.strip().lower()
        if p in PRIORITY_RANK:
            return p
        warnings.append(f"无效 priority: #{issue_number} 的 priority='{value}'，已按 p2 处理")
        return "p2"
    if value is None:
        return "p2"
    warnings.append(f"无效 priority: #{issue_number} 的 priority 类型异常，已按 p2 处理")
    return "p2"


def _run_gh_issue_body(issue_number: int, repo: Optional[str], warnings: list[str]) -> str:
    cmd = ["gh", "issue", "view", str(issue_number)]
    if repo:
        cmd += ["--repo", repo]
    cmd += ["--json", "body", "-q", ".body"]

    for attempt in range(2):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            if attempt == 0:
                continue
            warnings.append(f"无法获取 issue body（超时）: #{issue_number}（已忽略依赖解析）")
            return ""
        except Exception as e:
            warnings.append(f"无法获取 issue body: #{issue_number}: {e}（已忽略依赖解析）")
            return ""

        if result.returncode == 0:
            return result.stdout or ""

        if attempt == 0:
            continue
        detail = (result.stderr or "").strip() or (result.stdout or "").strip()
        detail = f": {detail}" if detail else ""
        warnings.append(f"无法获取 issue body: #{issue_number}{detail}（已忽略依赖解析）")
        return ""

    return ""


def _extract_dependencies(body: str, issue_number: int) -> set[int]:
    deps: set[int] = set()
    for m in DEP_RE.finditer(body or ""):
        try:
            dep = int(m.group(1))
        except ValueError:
            continue
        if dep != issue_number:
            deps.add(dep)
    return deps


def _topo_sort_with_fallback(
    nodes: list[int], deps_by_issue: dict[int, set[int]], batch_priority: str, warnings: list[str]
) -> list[int]:
    if len(nodes) <= 1:
        return nodes[:]

    node_set = set(nodes)
    indegree: dict[int, int] = {n: 0 for n in nodes}
    edges: dict[int, set[int]] = {n: set() for n in nodes}

    for issue in nodes:
        deps_in_batch = (deps_by_issue.get(issue) or set()) & node_set
        for dep in deps_in_batch:
            edges[dep].add(issue)
            indegree[issue] += 1

    heap: list[int] = [n for n in nodes if indegree[n] == 0]
    heapq.heapify(heap)
    ordered: list[int] = []

    while heap:
        n = heapq.heappop(heap)
        ordered.append(n)
        for nxt in edges.get(n, set()):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                heapq.heappush(heap, nxt)

    if len(ordered) != len(nodes):
        cycle_nodes = sorted(node_set - set(ordered))
        preview = " ".join(f"#{n}" for n in cycle_nodes[:10])
        suffix = " ..." if len(cycle_nodes) > 10 else ""
        warnings.append(
            f"循环依赖: {batch_priority.upper()} 批次存在循环依赖（{preview}{suffix}），已回退到按编号排序"
        )
        return sorted(nodes)

    return ordered


def main():
    parser = argparse.ArgumentParser(description="按 priority 分批并按依赖关系排序 Issues")
    parser.add_argument("--input", help="get_project_issues.py --json 的输出文件（默认从 stdin 读取）")
    parser.add_argument("--repo", help="用于 gh issue view 的仓库（默认使用当前仓库）")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    args = parser.parse_args()

    warnings: list[str] = []
    data = _read_json_input(args.input)

    raw_issues = data.get("issues")
    if not isinstance(raw_issues, list):
        print("Error: 输入 JSON 缺少 issues 列表", file=sys.stderr)
        sys.exit(1)

    prio_by_issue: dict[int, str] = {}
    for raw in raw_issues:
        if not isinstance(raw, dict):
            continue
        n = raw.get("number")
        if not isinstance(n, int):
            continue
        prio = _normalize_priority(raw.get("priority"), warnings, n)

        existing = prio_by_issue.get(n)
        if existing is None:
            prio_by_issue[n] = prio
            continue
        if PRIORITY_RANK[prio] < PRIORITY_RANK[existing]:
            warnings.append(f"重复 issue: #{n} 同时标记为 {existing.upper()} 和 {prio.upper()}，已选择 {prio.upper()}")
            prio_by_issue[n] = prio

    issue_numbers = sorted(prio_by_issue.keys())

    deps_by_issue: dict[int, set[int]] = {}
    for n in issue_numbers:
        body = _run_gh_issue_body(n, args.repo, warnings)
        deps_by_issue[n] = _extract_dependencies(body, n)

    # 跨批次依赖检测：仅对“高优先级依赖低优先级”给出警告（例如 P1 依赖 P2）。
    seen_cross: set[tuple[int, int]] = set()
    for issue, issue_prio in prio_by_issue.items():
        issue_rank = PRIORITY_RANK[issue_prio]
        for dep in deps_by_issue.get(issue, set()):
            dep_prio = prio_by_issue.get(dep)
            if not dep_prio:
                continue
            dep_rank = PRIORITY_RANK[dep_prio]
            if issue_rank < dep_rank and (issue, dep) not in seen_cross:
                seen_cross.add((issue, dep))
                warnings.append(
                    f"跨批次依赖: #{issue} ({issue_prio.upper()}) 依赖 #{dep} ({dep_prio.upper()})"
                )

    batches: list[dict[str, Any]] = []
    for p in PRIORITY_ORDER:
        nodes = sorted([n for n, prio in prio_by_issue.items() if prio == p])
        ordered = _topo_sort_with_fallback(nodes, deps_by_issue, p, warnings)
        batches.append({"priority": p, "issues": ordered})

    if args.json:
        print(json.dumps({"batches": batches, "warnings": warnings}, ensure_ascii=False, indent=2))
        return

    print("Batches:")
    for b in batches:
        issues = b["issues"]
        if issues:
            nums = " ".join(f"#{n}" for n in issues)
        else:
            nums = "(空)"
        print(f"- {b['priority']}: {nums}")
    if warnings:
        print("Warnings:")
        for w in warnings:
            print(f"- {w}")


if __name__ == "__main__":
    main()
