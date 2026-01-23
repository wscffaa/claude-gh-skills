#!/usr/bin/env python3
"""
CI Gate: 等待指定 commit 的 CI 通过。

实现要点:
- 使用 `gh api` 查询 GitHub REST API：
  - combined commit status: /repos/{owner}/{repo}/commits/{sha}/status
  - check runs: /repos/{owner}/{repo}/commits/{sha}/check-runs
- 兼容 `status`/`conclusion`（check runs / rollup）与 `state`（commit status）字段差异
- 提供 wait_for_ci_success()：支持超时、轮询间隔、失败快速退出
- 返回明确的 "success" | "failure" | "timeout" 状态
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from typing import Any, Callable, Literal, Optional, Sequence, Tuple

CIGateResult = Literal["success", "failure", "timeout"]
CIState = Literal["success", "failure", "pending"]

logger = logging.getLogger(__name__)


class GhApiError(RuntimeError):
    """`gh api` 调用失败或输出不可解析。"""


_DEFAULT_HEADERS: dict[str, str] = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

_CONCLUSION_SUCCESS = {"success", "neutral", "skipped"}
_CONCLUSION_FAILURE = {
    "failure",
    "cancelled",
    "canceled",
    "timed_out",
    "action_required",
    "startup_failure",
    "stale",
}
_STATUS_PENDING = {"queued", "in_progress", "pending"}


def _to_token(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def _parse_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        logger.warning("ci_gate.invalid_env_int", extra={"env": name, "value": raw})
        return default


def _parse_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    token = raw.strip().lower()
    if token in ("1", "true", "yes", "y", "on"):
        return True
    if token in ("0", "false", "no", "n", "off"):
        return False
    logger.warning("ci_gate.invalid_env_bool", extra={"env": name, "value": raw})
    return default


def _run_cmd(cmd: Sequence[str], *, timeout_s: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise GhApiError(f"command timed out: {' '.join(cmd)}") from e
    except FileNotFoundError as e:
        raise GhApiError("gh not found, please install and auth: gh auth login") from e
    except Exception as e:
        raise GhApiError(f"command failed: {e}") from e


def _gh_api_json(
    endpoint: str,
    *,
    timeout_s: int = 30,
    headers: Optional[dict[str, str]] = None,
) -> Any:
    """
    调用 `gh api <endpoint>` 并解析 JSON。

    Raises:
        GhApiError: 命令失败或 JSON 解析失败
    """
    merged_headers = dict(_DEFAULT_HEADERS)
    if headers:
        merged_headers.update(headers)

    cmd: list[str] = ["gh", "api"]
    for k, v in merged_headers.items():
        cmd.extend(["-H", f"{k}: {v}"])
    cmd.append(endpoint)

    result = _run_cmd(cmd, timeout_s=timeout_s)
    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()

    if result.returncode != 0:
        message = stderr or stdout or f"gh api failed: {endpoint}"
        raise GhApiError(message)

    try:
        return json.loads(stdout) if stdout else {}
    except json.JSONDecodeError as e:
        raise GhApiError(f"JSON parse error: {e}") from e


def _aggregate_states(states: list[CIState]) -> CIState:
    if any(s == "failure" for s in states):
        return "failure"
    if any(s == "pending" for s in states):
        return "pending"
    return "success"


def _normalize_commit_state(value: Any) -> Optional[CIState]:
    token = _to_token(value)
    if token == "success":
        return "success"
    if token in ("failure", "error"):
        return "failure"
    if token == "pending":
        return "pending"
    return None


def _normalize_check_item(item: dict[str, Any]) -> CIState:
    """
    将 check 结果归一化为 success/failure/pending。

    支持多来源字段差异：
    - REST check runs: status + conclusion
    - GraphQL rollup: status + conclusion
    - 其他来源: state（success/failure/pending）
    """
    conclusion = _to_token(item.get("conclusion"))
    if conclusion:
        if conclusion in _CONCLUSION_SUCCESS:
            return "success"
        if conclusion in _CONCLUSION_FAILURE:
            return "failure"
        return "pending"

    state = _to_token(item.get("state"))
    if state:
        if state in _CONCLUSION_SUCCESS or state == "success":
            return "success"
        if state in _CONCLUSION_FAILURE or state in ("failure", "error"):
            return "failure"
        if state in _STATUS_PENDING or state == "pending":
            return "pending"

    status = _to_token(item.get("status"))
    if status in _STATUS_PENDING:
        return "pending"
    if status == "completed":
        return "pending"

    return "pending"


def _summarize_commit_status(payload: Any) -> Tuple[CIState, int]:
    if not isinstance(payload, dict):
        return "pending", 0

    statuses = payload.get("statuses")
    states: list[CIState] = []
    if isinstance(statuses, list):
        for st in statuses:
            if not isinstance(st, dict):
                continue
            s = _normalize_commit_state(st.get("state"))
            if s:
                states.append(s)

    if states:
        return _aggregate_states(states), len(states)

    # Fallback: use combined state even if statuses is empty/unknown
    combined = _normalize_commit_state(payload.get("state"))
    return (combined or "pending"), 0


def _summarize_check_runs(payload: Any) -> Tuple[CIState, int]:
    if not isinstance(payload, dict):
        return "pending", 0

    runs = payload.get("check_runs")
    if not isinstance(runs, list):
        return "pending", 0

    states: list[CIState] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        states.append(_normalize_check_item(run))

    if not states:
        return "success", 0
    return _aggregate_states(states), len(states)


def get_ci_state(
    repo: str,
    sha: str,
    *,
    api_timeout_s: int = 30,
) -> CIState:
    """
    获取当前 CI 状态（success/failure/pending）。

    当 commit statuses 与 check runs 均为空时（无 CI 配置），返回 success。
    """
    status_payload = _gh_api_json(
        f"repos/{repo}/commits/{sha}/status",
        timeout_s=api_timeout_s,
    )
    checks_payload = _gh_api_json(
        f"repos/{repo}/commits/{sha}/check-runs",
        timeout_s=api_timeout_s,
    )

    commit_state, commit_count = _summarize_commit_status(status_payload)
    checks_state, checks_count = _summarize_check_runs(checks_payload)

    if commit_count == 0 and checks_count == 0:
        return "success"

    return _aggregate_states([commit_state, checks_state])


def wait_for_ci_success(
    repo: str,
    sha: str,
    *,
    timeout_s: Optional[int] = None,
    interval_s: Optional[int] = None,
    api_timeout_s: Optional[int] = None,
    fail_fast: Optional[bool] = None,
    time_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> CIGateResult:
    """
    等待 CI 通过。

    Args:
        repo: "owner/repo"
        sha: commit sha
        timeout_s: 总超时时间（秒），默认从 CI_GATE_TIMEOUT_S 读取，否则 600
        interval_s: 轮询间隔（秒），默认从 CI_GATE_INTERVAL_S 读取，否则 30
        api_timeout_s: 单次 API 调用超时（秒），默认从 CI_GATE_API_TIMEOUT_S 读取，否则 30
        fail_fast: 任一失败是否立即返回 failure；默认从 CI_GATE_FAIL_FAST 读取，否则 True
        time_fn/sleep_fn: 便于测试注入

    Returns:
        "success" | "failure" | "timeout"
    """
    if timeout_s is None:
        timeout_s = _parse_int_env("CI_GATE_TIMEOUT_S", 600)
    if interval_s is None:
        interval_s = _parse_int_env("CI_GATE_INTERVAL_S", 30)
    if api_timeout_s is None:
        api_timeout_s = _parse_int_env("CI_GATE_API_TIMEOUT_S", 30)
    if fail_fast is None:
        fail_fast = _parse_bool_env("CI_GATE_FAIL_FAST", True)

    interval_s = max(0, int(interval_s))
    timeout_s = max(0, int(timeout_s))
    api_timeout_s = max(1, int(api_timeout_s))

    start = time_fn()

    while True:
        try:
            state = get_ci_state(repo, sha, api_timeout_s=api_timeout_s)
        except Exception as e:
            logger.error(
                "ci_gate.api_error",
                extra={"repo": repo, "sha": sha, "error": str(e)},
            )
            return "failure"

        if state == "success":
            return "success"
        if state == "failure" and fail_fast:
            return "failure"

        if time_fn() - start >= timeout_s:
            return "timeout"

        if interval_s > 0:
            sleep_fn(interval_s)

