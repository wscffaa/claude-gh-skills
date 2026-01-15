#!/usr/bin/env python3
"""
批量审查并合并 PR。

用法:
    python3 batch_review.py --input sorted.json
    python3 batch_review.py --input sorted.json --auto-merge
    python3 batch_review.py --input sorted.json --parallel
    python3 batch_review.py --input sorted.json --max-retries 3

功能:
    - 串行调用 gh-pr-review 执行审查（默认）
    - 失败不阻塞，记录错误继续下一个
    - 支持 --parallel 参数启用并发
    - 支持 --auto-merge 参数

输入格式 (from sort_by_priority.py):
    {
      "sorted": [
        {"issue": 108, "pr": 112, "state": "open", "priority": "p0"}
      ]
    }

输出格式:
    {
      "results": [
        {"issue": 108, "pr": 112, "status": "merged", "error": null},
        {"issue": 109, "pr": 113, "status": "failed", "error": "CI failed"}
      ],
      "summary": {"total": 2, "merged": 1, "failed": 1}
    }
"""

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ReviewResult:
    """单个 PR 审查结果。"""

    issue: int
    pr: int
    status: str  # "merged", "approved", "failed", "skipped"
    error: Optional[str] = None
    duration_s: float = 0.0


@dataclass
class BatchSummary:
    """批量审查汇总。"""

    total: int = 0
    merged: int = 0
    approved: int = 0
    failed: int = 0
    skipped: int = 0


def _run_gh(cmd: list[str], timeout: int = 300) -> tuple[int, str, str]:
    """运行 gh 命令并返回 (returncode, stdout, stderr)。"""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "command timed out"
    except Exception as e:
        return -1, "", str(e)


def _run_gh_json(cmd: list[str], timeout: int = 60) -> Optional[dict | list]:
    """运行 gh 命令并解析 JSON 输出。"""
    returncode, stdout, stderr = _run_gh(cmd, timeout)
    if returncode != 0:
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None


def check_pr_status(pr_number: int) -> dict:
    """
    检查 PR 当前状态。

    返回:
        {"state": "open|merged|closed", "mergeable": bool, "ci_status": "pass|fail|pending"}
    """
    data = _run_gh_json(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--json",
            "state,mergeable,mergeStateStatus,statusCheckRollup",
        ],
        timeout=30,
    )

    if not data or not isinstance(data, dict):
        return {"state": "unknown", "mergeable": False, "ci_status": "unknown"}

    state = data.get("state", "").upper()
    if state == "MERGED":
        return {"state": "merged", "mergeable": False, "ci_status": "pass"}
    elif state == "CLOSED":
        return {"state": "closed", "mergeable": False, "ci_status": "unknown"}

    # 检查 CI 状态
    checks = data.get("statusCheckRollup") or []
    ci_status = "pass"
    has_pending = False

    for check in checks:
        conclusion = (check.get("conclusion") or "").upper()
        status = (check.get("status") or "").upper()

        if status in ("QUEUED", "IN_PROGRESS", "PENDING"):
            has_pending = True
        elif conclusion in ("FAILURE", "CANCELLED", "TIMED_OUT"):
            ci_status = "fail"
            break

    if ci_status == "pass" and has_pending:
        ci_status = "pending"

    mergeable = data.get("mergeable", "") == "MERGEABLE"

    return {"state": "open", "mergeable": mergeable, "ci_status": ci_status}


def wait_for_ci(pr_number: int, timeout_s: int = 600, interval_s: int = 30) -> str:
    """
    等待 CI 完成。

    返回: "pass" | "fail" | "timeout"
    """
    start = time.time()
    while time.time() - start < timeout_s:
        status = check_pr_status(pr_number)
        ci = status.get("ci_status", "unknown")

        if ci == "pass":
            return "pass"
        elif ci == "fail":
            return "fail"

        # CI pending，继续等待
        time.sleep(interval_s)

    return "timeout"


def approve_pr(pr_number: int) -> tuple[bool, str]:
    """
    批准 PR。

    返回: (success, error_message)
    """
    returncode, stdout, stderr = _run_gh(
        [
            "gh",
            "pr",
            "review",
            str(pr_number),
            "--approve",
            "--body",
            "Approved by batch_review.py\n\nReviewed by Claude Code",
        ],
        timeout=60,
    )

    if returncode == 0:
        return True, ""
    return False, stderr.strip() or "approve failed"


def merge_pr(pr_number: int, squash: bool = True) -> tuple[bool, str]:
    """
    合并 PR。

    返回: (success, error_message)
    """
    cmd = ["gh", "pr", "merge", str(pr_number), "--delete-branch"]
    if squash:
        cmd.append("--squash")
    else:
        cmd.append("--merge")

    returncode, stdout, stderr = _run_gh(cmd, timeout=120)

    if returncode == 0:
        return True, ""
    return False, stderr.strip() or "merge failed"


def review_single_pr(
    issue: int,
    pr: int,
    auto_merge: bool = False,
    max_retries: int = 1,
    verbose: bool = False,
) -> ReviewResult:
    """
    审查单个 PR。

    流程:
    1. 检查 PR 状态
    2. 等待 CI（如果 pending）
    3. 批准 PR
    4. 合并 PR（如果 --auto-merge）

    失败不抛异常，记录错误返回结果。
    """
    start_time = time.time()

    def log(msg: str):
        if verbose:
            print(f"  [PR #{pr}] {msg}", file=sys.stderr)

    # Phase 1: 检查状态
    log("检查 PR 状态...")
    status = check_pr_status(pr)

    if status["state"] == "merged":
        log("已合并，跳过")
        return ReviewResult(
            issue=issue,
            pr=pr,
            status="skipped",
            error="already merged",
            duration_s=time.time() - start_time,
        )

    if status["state"] == "closed":
        log("已关闭，跳过")
        return ReviewResult(
            issue=issue,
            pr=pr,
            status="skipped",
            error="PR is closed",
            duration_s=time.time() - start_time,
        )

    # Phase 2: 等待 CI
    if status["ci_status"] == "pending":
        log("CI 运行中，等待...")
        ci_result = wait_for_ci(pr, timeout_s=600, interval_s=30)
        if ci_result == "timeout":
            return ReviewResult(
                issue=issue,
                pr=pr,
                status="failed",
                error="CI timeout",
                duration_s=time.time() - start_time,
            )
        elif ci_result == "fail":
            return ReviewResult(
                issue=issue,
                pr=pr,
                status="failed",
                error="CI failed",
                duration_s=time.time() - start_time,
            )
    elif status["ci_status"] == "fail":
        return ReviewResult(
            issue=issue,
            pr=pr,
            status="failed",
            error="CI failed",
            duration_s=time.time() - start_time,
        )

    # Phase 3: 批准 PR
    log("批准 PR...")
    for attempt in range(max_retries):
        success, err = approve_pr(pr)
        if success:
            break
        if attempt < max_retries - 1:
            log(f"批准失败 ({err})，重试 {attempt + 2}/{max_retries}...")
            time.sleep(5)
    else:
        return ReviewResult(
            issue=issue,
            pr=pr,
            status="failed",
            error=f"approve failed: {err}",
            duration_s=time.time() - start_time,
        )

    # Phase 4: 合并（如果 auto_merge）
    if auto_merge:
        log("合并 PR...")
        # 重新检查 mergeable 状态
        status = check_pr_status(pr)
        if not status.get("mergeable", False):
            # 可能需要等待 approval 生效
            time.sleep(3)
            status = check_pr_status(pr)

        for attempt in range(max_retries):
            success, err = merge_pr(pr)
            if success:
                log("合并成功")
                return ReviewResult(
                    issue=issue,
                    pr=pr,
                    status="merged",
                    error=None,
                    duration_s=time.time() - start_time,
                )
            if attempt < max_retries - 1:
                log(f"合并失败 ({err})，重试 {attempt + 2}/{max_retries}...")
                time.sleep(5)

        return ReviewResult(
            issue=issue,
            pr=pr,
            status="failed",
            error=f"merge failed: {err}",
            duration_s=time.time() - start_time,
        )

    # 只批准不合并
    log("批准完成")
    return ReviewResult(
        issue=issue,
        pr=pr,
        status="approved",
        error=None,
        duration_s=time.time() - start_time,
    )


def batch_review_serial(
    items: list[dict],
    auto_merge: bool = False,
    max_retries: int = 1,
    verbose: bool = False,
) -> list[ReviewResult]:
    """
    串行批量审查。

    失败不阻塞，记录错误继续下一个。
    """
    results = []

    for i, item in enumerate(items, 1):
        issue = item.get("issue")
        pr = item.get("pr")

        if not pr:
            if verbose:
                print(
                    f"[{i}/{len(items)}] Issue #{issue} 无关联 PR，跳过", file=sys.stderr
                )
            results.append(
                ReviewResult(issue=issue, pr=0, status="skipped", error="no PR linked")
            )
            continue

        if verbose:
            print(f"[{i}/{len(items)}] 审查 Issue #{issue} -> PR #{pr}", file=sys.stderr)

        result = review_single_pr(
            issue=issue,
            pr=pr,
            auto_merge=auto_merge,
            max_retries=max_retries,
            verbose=verbose,
        )
        results.append(result)

        if verbose:
            status_str = result.status
            if result.error:
                status_str += f" ({result.error})"
            print(
                f"  结果: {status_str} (耗时 {result.duration_s:.1f}s)", file=sys.stderr
            )

    return results


def batch_review_parallel(
    items: list[dict],
    auto_merge: bool = False,
    max_retries: int = 1,
    max_workers: int = 4,
    verbose: bool = False,
) -> list[ReviewResult]:
    """
    并行批量审查。

    使用 ThreadPoolExecutor 并发执行。
    注意：并行模式下 verbose 输出可能交错。
    """
    results = []

    # 过滤有 PR 的项
    valid_items = []
    for item in items:
        issue = item.get("issue")
        pr = item.get("pr")
        if not pr:
            results.append(
                ReviewResult(issue=issue, pr=0, status="skipped", error="no PR linked")
            )
        else:
            valid_items.append(item)

    if not valid_items:
        return results

    if verbose:
        print(f"并行审查 {len(valid_items)} 个 PR (workers={max_workers})", file=sys.stderr)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_item = {}
        for item in valid_items:
            future = executor.submit(
                review_single_pr,
                issue=item["issue"],
                pr=item["pr"],
                auto_merge=auto_merge,
                max_retries=max_retries,
                verbose=verbose,
            )
            future_to_item[future] = item

        for future in as_completed(future_to_item):
            item = future_to_item[future]
            try:
                result = future.result()
                results.append(result)
                if verbose:
                    print(
                        f"  PR #{item['pr']}: {result.status} ({result.duration_s:.1f}s)",
                        file=sys.stderr,
                    )
            except Exception as e:
                results.append(
                    ReviewResult(
                        issue=item["issue"],
                        pr=item["pr"],
                        status="failed",
                        error=str(e),
                    )
                )

    return results


def summarize_results(results: list[ReviewResult]) -> BatchSummary:
    """汇总审查结果。"""
    summary = BatchSummary(total=len(results))

    for r in results:
        if r.status == "merged":
            summary.merged += 1
        elif r.status == "approved":
            summary.approved += 1
        elif r.status == "failed":
            summary.failed += 1
        elif r.status == "skipped":
            summary.skipped += 1

    return summary


def format_output(results: list[ReviewResult], summary: BatchSummary) -> dict:
    """格式化输出为指定 JSON 结构。"""
    return {
        "results": [
            {
                "issue": r.issue,
                "pr": r.pr,
                "status": r.status,
                "error": r.error,
                "duration_s": round(r.duration_s, 2),
            }
            for r in results
        ],
        "summary": {
            "total": summary.total,
            "merged": summary.merged,
            "approved": summary.approved,
            "failed": summary.failed,
            "skipped": summary.skipped,
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description="批量审查并合并 PR",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 串行审查（只批准，不合并）
  python3 batch_review.py --input sorted.json

  # 串行审查并自动合并
  python3 batch_review.py --input sorted.json --auto-merge

  # 并行审查
  python3 batch_review.py --input sorted.json --parallel

  # 设置最大重试次数
  python3 batch_review.py --input sorted.json --max-retries 3
""",
    )
    parser.add_argument(
        "--input",
        required=True,
        help="输入 JSON 文件路径（from sort_by_priority.py）",
    )
    parser.add_argument(
        "--auto-merge",
        action="store_true",
        help="审查通过后自动合并",
    )
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="启用并发模式",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=4,
        help="并发模式下的最大 worker 数（默认: 4）",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=1,
        help="每个 PR 的最大重试次数（默认: 1）",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="输出详细日志到 stderr",
    )

    args = parser.parse_args()

    # 读取输入文件
    try:
        with open(args.input, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Error: 文件不存在: {args.input}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: JSON 解析失败: {e}", file=sys.stderr)
        sys.exit(1)

    # 支持两种输入格式:
    # 1. {"sorted": [...]} (from sort_by_priority.py)
    # 2. {"mappings": [...]} (from get_project_prs.py)
    items = data.get("sorted") or data.get("mappings") or []

    if not items:
        print("Warning: 输入为空，无 PR 需要审查", file=sys.stderr)
        output = format_output([], BatchSummary())
        print(json.dumps(output, ensure_ascii=False, indent=2))
        sys.exit(0)

    # 过滤只保留 open 状态的 PR
    open_items = [
        item
        for item in items
        if item.get("pr") and item.get("state", "open").lower() == "open"
    ]

    if args.verbose:
        print(f"输入: {len(items)} 项，其中 {len(open_items)} 个 open PR", file=sys.stderr)

    # 执行批量审查
    if args.parallel:
        results = batch_review_parallel(
            items=open_items,
            auto_merge=args.auto_merge,
            max_retries=args.max_retries,
            max_workers=args.max_workers,
            verbose=args.verbose,
        )
    else:
        results = batch_review_serial(
            items=open_items,
            auto_merge=args.auto_merge,
            max_retries=args.max_retries,
            verbose=args.verbose,
        )

    # 汇总并输出
    summary = summarize_results(results)
    output = format_output(results, summary)

    print(json.dumps(output, ensure_ascii=False, indent=2))

    # 如果有失败，返回非零退出码
    if summary.failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
