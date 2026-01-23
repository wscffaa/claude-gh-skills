#!/usr/bin/env python3
"""
Codex 审查执行器。

功能:
1) 使用 gh 拉取 PR diff/files 上下文
2) 调用 codeagent-wrapper --backend codex 执行审查
3) 解析结构化 JSON verdict:
   {"approved": bool, "blocking": [], "summary": str, "confidence": float}
4) 处理异常与超时重试
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)

REQUIRED_VERDICT_KEYS: set[str] = {"approved", "blocking", "summary", "confidence"}


@dataclass(frozen=True)
class PRContext:
    pr_number: int
    title: str
    body: str
    files: list[str]
    diff: str


def _run_cmd(
    cmd: Sequence[str],
    *,
    input_text: Optional[str] = None,
    timeout_s: int,
) -> tuple[int, str, str]:
    """运行命令并返回 (returncode, stdout, stderr)。"""
    try:
        result = subprocess.run(
            list(cmd),
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        return result.returncode, result.stdout or "", result.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", "command timed out"
    except FileNotFoundError as e:
        return -1, "", f"command not found: {e}"
    except Exception as e:
        return -1, "", str(e)


def _run_gh(cmd: list[str], *, timeout_s: int) -> tuple[int, str, str]:
    """运行 gh 命令。"""
    return _run_cmd(cmd, timeout_s=timeout_s)


def _run_gh_json(cmd: list[str], *, timeout_s: int) -> Optional[dict[str, Any] | list[Any]]:
    """运行 gh 命令并解析 JSON 输出，失败返回 None。"""
    returncode, stdout, stderr = _run_gh(cmd, timeout_s=timeout_s)
    if returncode != 0:
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None


def fetch_pr_context(
    pr_number: int,
    *,
    repo: Optional[str] = None,
    timeout_s: int = 60,
) -> PRContext:
    """
    拉取 PR 上下文（title/body/files/diff）。

    Args:
        pr_number: PR number
        repo: 可选，owner/repo
        timeout_s: gh 命令超时
    """
    view_cmd = [
        "gh",
        "pr",
        "view",
        str(pr_number),
        "--json",
        "title,body,files",
    ]
    if repo:
        view_cmd.extend(["--repo", repo])

    view_data = _run_gh_json(view_cmd, timeout_s=timeout_s)
    if not isinstance(view_data, dict):
        raise RuntimeError("gh pr view failed")

    title = str(view_data.get("title") or "")
    body = str(view_data.get("body") or "")

    files: list[str] = []
    raw_files = view_data.get("files") or []
    if isinstance(raw_files, list):
        for item in raw_files:
            if isinstance(item, dict) and item.get("path"):
                files.append(str(item["path"]))
            elif isinstance(item, str) and item.strip():
                files.append(item.strip())

    diff_cmd = ["gh", "pr", "diff", str(pr_number)]
    if repo:
        diff_cmd.extend(["--repo", repo])

    returncode, stdout, stderr = _run_gh(diff_cmd, timeout_s=timeout_s)
    if returncode != 0:
        raise RuntimeError(stderr.strip() or stdout.strip() or "gh pr diff failed")

    return PRContext(pr_number=pr_number, title=title, body=body, files=files, diff=stdout)


def _build_review_prompt(ctx: PRContext) -> str:
    files_text = "\n".join(f"- {path}" for path in ctx.files) if ctx.files else "(no files)"
    return (
        "You are a strict pull request reviewer.\n"
        "Review the PR diff and changed files context below.\n\n"
        "Return ONLY a valid JSON object with EXACT keys:\n"
        '  {"approved": bool, "blocking": [str], "summary": str, "confidence": float}\n'
        "- approved: true ONLY if safe to merge.\n"
        "- blocking: list concrete merge-blocking issues (include file hints).\n"
        "- summary: 1-3 sentences.\n"
        "- confidence: 0.0..1.0\n\n"
        "Do NOT output markdown/code fences or any extra text.\n\n"
        f"PR #{ctx.pr_number}\n"
        f"Title: {ctx.title}\n\n"
        "Body:\n"
        f"{ctx.body}\n\n"
        "Changed files:\n"
        f"{files_text}\n\n"
        "Diff:\n"
        "```diff\n"
        f"{ctx.diff}\n"
        "```\n"
    )


def _is_retryable_error(returncode: int, stdout: str, stderr: str) -> bool:
    msg = (stderr or stdout or "").lower()
    if returncode == -1:
        return "timed out" in msg or "timeout" in msg
    return any(
        token in msg
        for token in (
            "timed out",
            "timeout",
            "network",
            "connection reset",
            "connection refused",
            "connection aborted",
            "temporarily unavailable",
            "temporary failure",
            "tls",
        )
    )


def _call_codeagent_wrapper(
    prompt: str,
    *,
    backend: str = "codex",
    workdir: str = ".",
    timeout_s: int = 600,
    max_retries: int = 1,
) -> tuple[str, str]:
    """
    调用 codeagent-wrapper 并返回 (stdout, error_message)。

    - 使用 stdin 传入 prompt：`codeagent-wrapper --backend codex - <workdir>`
    - 遇到超时/网络类错误时按 max_retries 重试
    """
    cmd = ["codeagent-wrapper", "--backend", backend, "-", workdir]

    attempts = max_retries + 1
    last_error = ""

    for attempt in range(attempts):
        returncode, stdout, stderr = _run_cmd(cmd, input_text=prompt, timeout_s=timeout_s)

        if returncode == 0:
            return stdout, ""

        last_error = (stderr or stdout or f"codeagent-wrapper failed (rc={returncode})").strip()

        if attempt < attempts - 1 and _is_retryable_error(returncode, stdout, stderr):
            time.sleep(min(2**attempt, 10))
            continue

        break

    return "", last_error


def _extract_json_object(text: str) -> Optional[dict[str, Any]]:
    """从输出中提取第一个 JSON object（dict）。"""
    if not text:
        return None

    trimmed = text.strip()
    try:
        parsed = json.loads(trimmed)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    scan_index = 0
    while True:
        start = trimmed.find("{", scan_index)
        if start < 0:
            return None
        try:
            parsed, _end = decoder.raw_decode(trimmed[start:])
        except json.JSONDecodeError:
            scan_index = start + 1
            continue
        if isinstance(parsed, dict):
            return parsed
        scan_index = start + 1


def parse_verdict(output_text: str) -> dict[str, Any]:
    """解析并校验 verdict JSON。"""
    obj = _extract_json_object(output_text)
    if not obj:
        raise ValueError("no JSON object found")

    missing = REQUIRED_VERDICT_KEYS - set(obj.keys())
    if missing:
        raise ValueError(f"missing keys: {sorted(missing)}")

    approved = obj.get("approved")
    if not isinstance(approved, bool):
        raise ValueError("approved must be bool")

    blocking_raw = obj.get("blocking")
    if not isinstance(blocking_raw, list):
        raise ValueError("blocking must be list")
    blocking = [str(item).strip() for item in blocking_raw if str(item).strip()]

    summary = obj.get("summary")
    if not isinstance(summary, str):
        raise ValueError("summary must be str")

    confidence_raw = obj.get("confidence")
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        raise ValueError("confidence must be a number")

    if confidence != confidence:  # NaN
        raise ValueError("confidence must not be NaN")

    confidence = max(0.0, min(1.0, confidence))

    return {
        "approved": approved,
        "blocking": blocking,
        "summary": summary.strip(),
        "confidence": confidence,
    }


def review_pr_with_codex(
    pr_number: int,
    *,
    repo: Optional[str] = None,
    backend: str = "codex",
    gh_timeout_s: int = 60,
    codex_timeout_s: int = 600,
    max_retries: int = 1,
    workdir: str = ".",
    confidence_warn_threshold: float = 0.5,
) -> dict[str, Any]:
    """
    执行 PR 审查并返回结构化 verdict。

    返回:
        {"approved": bool, "blocking": [], "summary": str, "confidence": float}
    """
    try:
        ctx = fetch_pr_context(pr_number, repo=repo, timeout_s=gh_timeout_s)
    except Exception as e:
        return {
            "approved": False,
            "blocking": [f"pr_context_error: {e}"],
            "summary": "Failed to fetch PR context",
            "confidence": 0.0,
        }

    prompt = _build_review_prompt(ctx)
    stdout, error = _call_codeagent_wrapper(
        prompt,
        backend=backend,
        workdir=workdir,
        timeout_s=codex_timeout_s,
        max_retries=max_retries,
    )

    if error:
        return {
            "approved": False,
            "blocking": [f"codex_call_error: {error}"],
            "summary": "Codex review failed",
            "confidence": 0.0,
        }

    try:
        verdict = parse_verdict(stdout)
    except Exception as e:
        return {
            "approved": False,
            "blocking": [f"verdict_parse_error: {e}"],
            "summary": "Codex verdict JSON parse failed",
            "confidence": 0.0,
        }

    if verdict["confidence"] < confidence_warn_threshold:
        logger.warning(
            "Codex confidence below threshold: pr=%s confidence=%.3f",
            pr_number,
            verdict["confidence"],
        )

    return verdict

