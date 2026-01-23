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
import importlib.util
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
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


def _load_sibling_module(module_name: str, filename: str):
    """
    以固定模块名加载同目录下的模块文件。

    说明：测试中使用 `scripts/<name>` 作为 module key 以便 pytest-cov 采集覆盖率，
    此处沿用相同命名，避免重复加载。
    """
    if module_name in sys.modules:
        return sys.modules[module_name]

    module_path = Path(__file__).resolve().parent / filename
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if not spec or not spec.loader:
        raise ImportError(f"failed to load module: {filename}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


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


def get_pr_metadata(pr_number: int) -> dict:
    """
    获取 PR 元信息（state/mergeable/head repo+sha+branch）。

    返回:
        {
          "state": "open|merged|closed|unknown",
          "mergeable": bool,
          "head_repo": Optional[str],
          "head_sha": Optional[str],
          "head_ref": Optional[str],
        }
    """
    data = _run_gh_json(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--json",
            "state,mergeable,headRefOid,headRefName,headRepository,headRepositoryOwner",
        ],
        timeout=30,
    )

    if not isinstance(data, dict):
        return {
            "state": "unknown",
            "mergeable": False,
            "head_repo": None,
            "head_sha": None,
            "head_ref": None,
        }

    state_raw = str(data.get("state") or "").upper()
    if state_raw == "MERGED":
        state = "merged"
    elif state_raw == "CLOSED":
        state = "closed"
    elif state_raw:
        state = "open"
    else:
        state = "unknown"

    mergeable = str(data.get("mergeable") or "") == "MERGEABLE"

    head_repo = None
    head_repo_raw = data.get("headRepository")
    head_owner_raw = data.get("headRepositoryOwner")
    if isinstance(head_repo_raw, dict):
        nwo = head_repo_raw.get("nameWithOwner")
        if nwo:
            head_repo = str(nwo)
        else:
            # Construct from headRepositoryOwner + headRepository.name
            head_name = head_repo_raw.get("name")
            owner = None
            if isinstance(head_owner_raw, dict):
                owner = head_owner_raw.get("login")
            if owner and head_name:
                head_repo = f"{owner}/{head_name}"

    head_sha = data.get("headRefOid")
    if head_sha is not None:
        head_sha = str(head_sha)

    head_ref = data.get("headRefName")
    if head_ref is not None:
        head_ref = str(head_ref)

    return {
        "state": state,
        "mergeable": mergeable,
        "head_repo": head_repo,
        "head_sha": head_sha,
        "head_ref": head_ref,
    }


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
    cmd = ["gh", "pr", "merge", str(pr_number), "--yes"]
    if squash:
        cmd.append("--squash")
    else:
        cmd.append("--merge")

    returncode, stdout, stderr = _run_gh(cmd, timeout=120)

    if returncode == 0:
        return True, ""
    return False, stderr.strip() or "merge failed"


def delete_branch(repo: str, branch: str) -> tuple[bool, str]:
    """
    删除远端分支（通过 GitHub API）。

    返回: (success, error_message)
    """
    if not repo or not branch:
        return False, "missing repo or branch"

    # GitHub API: DELETE /repos/{owner}/{repo}/git/refs/heads/{ref}
    cmd = ["gh", "api", "-X", "DELETE", f"repos/{repo}/git/refs/heads/{branch}"]
    returncode, stdout, stderr = _run_gh(cmd, timeout=60)

    if returncode == 0:
        return True, ""

    msg = (stderr or "").strip() or (stdout or "").strip() or "delete branch failed"
    return False, msg


def review_single_pr(
    issue: int,
    pr: int,
    auto_merge: bool = False,
    review_backend: str = "codex",
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

    # Phase 1: 获取 PR 元信息
    log("检查 PR 元信息...")
    meta = get_pr_metadata(pr)

    if meta["state"] == "merged":
        log("已合并，跳过")
        return ReviewResult(
            issue=issue,
            pr=pr,
            status="skipped",
            error="already merged",
            duration_s=time.time() - start_time,
        )

    if meta["state"] == "closed":
        log("已关闭，跳过")
        return ReviewResult(
            issue=issue,
            pr=pr,
            status="skipped",
            error="PR is closed",
            duration_s=time.time() - start_time,
        )

    if meta["state"] != "open":
        return ReviewResult(
            issue=issue,
            pr=pr,
            status="failed",
            error=f"unknown PR state: {meta['state']}",
            duration_s=time.time() - start_time,
        )

    # Phase 2: Codex review gate
    log("Codex review gate...")
    try:
        codex_review = _load_sibling_module("scripts/codex_review", "codex_review.py")
        verdict = codex_review.review_pr_with_codex(
            pr,
            backend=review_backend,
            max_retries=max_retries,
            workdir=str(Path(__file__).resolve().parents[1]),
        )
    except Exception as e:
        return ReviewResult(
            issue=issue,
            pr=pr,
            status="failed",
            error=f"codex review error: {e}",
            duration_s=time.time() - start_time,
        )

    if not isinstance(verdict, dict) or not verdict.get("approved", False):
        blocking = None
        summary = None
        if isinstance(verdict, dict):
            blocking = verdict.get("blocking")
            summary = verdict.get("summary")
        detail = ""
        if summary:
            detail = f"summary={summary}"
        if blocking:
            joined = ", ".join(str(x) for x in blocking) if isinstance(blocking, list) else str(blocking)
            detail = (detail + "; " if detail else "") + f"blocking={joined}"
        return ReviewResult(
            issue=issue,
            pr=pr,
            status="failed",
            error=f"codex not approved{(': ' + detail) if detail else ''}",
            duration_s=time.time() - start_time,
        )

    # Phase 3: CI gate
    head_repo = meta.get("head_repo")
    if not head_repo:
        return ReviewResult(
            issue=issue,
            pr=pr,
            status="failed",
            error="missing PR head repo for CI gate",
            duration_s=time.time() - start_time,
        )

    log("CI gate...")
    try:
        ci_gate = _load_sibling_module("scripts/ci_gate", "ci_gate.py")
        returncode, stdout, stderr = _run_gh(
            [
                "gh",
                "pr",
                "view",
                str(pr),
                "--json",
                "headRefOid",
                "-q",
                ".headRefOid",
            ],
            timeout=30,
        )
        head_sha = (stdout or "").strip() if returncode == 0 else ""
        if not head_sha:
            msg = (stderr or "").strip() or "failed to get PR head sha"
            raise RuntimeError(msg)

        ci_state = ci_gate.get_ci_state(head_repo, head_sha)
    except Exception as e:
        return ReviewResult(
            issue=issue,
            pr=pr,
            status="failed",
            error=f"ci gate error: {e}",
            duration_s=time.time() - start_time,
        )

    if ci_state == "pending":
        return ReviewResult(
            issue=issue,
            pr=pr,
            status="failed",
            error="CI pending",
            duration_s=time.time() - start_time,
        )
    if ci_state != "success":
        return ReviewResult(
            issue=issue,
            pr=pr,
            status="failed",
            error="CI failed",
            duration_s=time.time() - start_time,
        )

    # Phase 4: 批准 PR
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

    # Phase 5: 合并（仅在 Codex approved + CI success + auto_merge 时执行）
    if auto_merge:
        log("合并 PR (squash)...")
        # 重新检查 mergeable 状态
        meta = get_pr_metadata(pr)
        if not meta.get("mergeable", False):
            # 可能需要等待 approval 生效
            time.sleep(3)
            meta = get_pr_metadata(pr)

        for attempt in range(max_retries):
            success, err = merge_pr(pr)
            if success:
                log("合并成功，删除分支...")
                cleanup_error = None
                head_repo = meta.get("head_repo")
                head_ref = meta.get("head_ref")
                if head_repo and head_ref:
                    deleted, del_err = delete_branch(head_repo, head_ref)
                    if not deleted:
                        cleanup_error = f"branch cleanup failed: {del_err}"
                else:
                    cleanup_error = "branch cleanup failed: missing head repo/ref"
                return ReviewResult(
                    issue=issue,
                    pr=pr,
                    status="merged",
                    error=cleanup_error,
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
    review_backend: str = "codex",
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
            review_backend=review_backend,
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
    review_backend: str = "codex",
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
                review_backend=review_backend,
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
        "--review-backend",
        default="codex",
        help="Review backend: codex/claude",
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
            review_backend=args.review_backend,
            max_retries=args.max_retries,
            max_workers=args.max_workers,
            verbose=args.verbose,
        )
    else:
        results = batch_review_serial(
            items=open_items,
            auto_merge=args.auto_merge,
            review_backend=args.review_backend,
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
