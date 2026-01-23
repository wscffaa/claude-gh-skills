import json
import logging
import subprocess
import sys
import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
MODULE_NAME = "scripts/codex_review"
SPEC = importlib.util.spec_from_file_location(MODULE_NAME, SCRIPTS_DIR / "codex_review.py")
assert SPEC and SPEC.loader
codex_review = importlib.util.module_from_spec(SPEC)
sys.modules[MODULE_NAME] = codex_review
SPEC.loader.exec_module(codex_review)


def _install_fake_subprocess_run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pr_number: int,
    gh_view_payload: dict,
    gh_diff_text: str,
    codeagent_sequence: list[object],
) -> dict[str, int]:
    """
    安装 subprocess.run fake，实现:
    - gh pr view / gh pr diff 返回固定内容
    - codeagent-wrapper 按序返回 CompletedProcess 或抛异常
    """
    counters = {"codeagent_calls": 0}

    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "pr", "view"]:
            assert str(pr_number) in args
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps(gh_view_payload), stderr="")

        if args[:3] == ["gh", "pr", "diff"]:
            assert str(pr_number) in args
            return subprocess.CompletedProcess(args, 0, stdout=gh_diff_text, stderr="")

        if args and args[0] == "codeagent-wrapper":
            assert args[:4] == ["codeagent-wrapper", "--backend", "codex", "-"]
            counters["codeagent_calls"] += 1
            item = codeagent_sequence[counters["codeagent_calls"] - 1]
            if isinstance(item, BaseException):
                raise item
            return item

        raise AssertionError(f"Unexpected command: {args!r}")

    monkeypatch.setattr(codex_review.subprocess, "run", fake_run)
    return counters


def test_review_pr_parses_approved_true(monkeypatch: pytest.MonkeyPatch):
    pr_number = 123
    gh_view_payload = {
        "title": "Add feature",
        "body": "Closes #1",
        "files": [{"path": "a.py"}, {"path": "b.py"}],
    }
    gh_diff_text = "diff --git a/a.py b/a.py\n+print('hi')\n"

    expected = {"approved": True, "blocking": [], "summary": "LGTM", "confidence": 0.9}
    codeagent_ok = subprocess.CompletedProcess(
        ["codeagent-wrapper", "--backend", "codex", "-", "."],
        0,
        stdout=json.dumps(expected),
        stderr="",
    )

    _install_fake_subprocess_run(
        monkeypatch,
        pr_number=pr_number,
        gh_view_payload=gh_view_payload,
        gh_diff_text=gh_diff_text,
        codeagent_sequence=[codeagent_ok],
    )

    verdict = codex_review.review_pr_with_codex(pr_number, max_retries=0, workdir=".")
    assert verdict == expected


def test_review_pr_parses_approved_false_and_blocking(monkeypatch: pytest.MonkeyPatch):
    pr_number = 456
    gh_view_payload = {
        "title": "Refactor",
        "body": "",
        "files": [{"path": "x.py"}, "y.py"],
    }
    gh_diff_text = "diff --git a/x.py b/x.py\n-bug()\n+bug_fixed()\n"

    expected = {
        "approved": False,
        "blocking": ["x.py: possible regression", "missing tests"],
        "summary": "Need fixes",
        "confidence": 0.8,
    }
    codeagent_ok = subprocess.CompletedProcess(
        ["codeagent-wrapper", "--backend", "codex", "-", "."],
        0,
        stdout=json.dumps(expected),
        stderr="",
    )

    _install_fake_subprocess_run(
        monkeypatch,
        pr_number=pr_number,
        gh_view_payload=gh_view_payload,
        gh_diff_text=gh_diff_text,
        codeagent_sequence=[codeagent_ok],
    )

    verdict = codex_review.review_pr_with_codex(pr_number, max_retries=0, workdir=".")
    assert verdict == expected
    assert verdict["approved"] is False
    assert verdict["blocking"]


def test_review_pr_json_parse_error_returns_not_approved(monkeypatch: pytest.MonkeyPatch):
    pr_number = 789
    gh_view_payload = {"title": "Oops", "body": "", "files": [{"path": "z.py"}]}
    gh_diff_text = "diff --git a/z.py b/z.py\n+oops\n"

    codeagent_bad = subprocess.CompletedProcess(
        ["codeagent-wrapper", "--backend", "codex", "-", "."],
        0,
        stdout="not-json",
        stderr="",
    )

    _install_fake_subprocess_run(
        monkeypatch,
        pr_number=pr_number,
        gh_view_payload=gh_view_payload,
        gh_diff_text=gh_diff_text,
        codeagent_sequence=[codeagent_bad],
    )

    verdict = codex_review.review_pr_with_codex(pr_number, max_retries=0, workdir=".")
    assert verdict["approved"] is False
    assert verdict["confidence"] == 0.0
    assert verdict["summary"] == "Codex verdict JSON parse failed"
    assert verdict["blocking"] and "verdict_parse_error:" in verdict["blocking"][0]


def test_timeout_retries_once_then_fails(monkeypatch: pytest.MonkeyPatch):
    pr_number = 101
    gh_view_payload = {"title": "Timeout case", "body": "", "files": [{"path": "t.py"}]}
    gh_diff_text = "diff --git a/t.py b/t.py\n+1\n"

    timeout_exc_1 = subprocess.TimeoutExpired(cmd=["codeagent-wrapper"], timeout=1)
    timeout_exc_2 = subprocess.TimeoutExpired(cmd=["codeagent-wrapper"], timeout=1)

    counters = _install_fake_subprocess_run(
        monkeypatch,
        pr_number=pr_number,
        gh_view_payload=gh_view_payload,
        gh_diff_text=gh_diff_text,
        codeagent_sequence=[timeout_exc_1, timeout_exc_2],
    )
    monkeypatch.setattr(codex_review.time, "sleep", lambda _seconds: None)

    verdict = codex_review.review_pr_with_codex(
        pr_number,
        max_retries=1,
        workdir=".",
        codex_timeout_s=1,
    )
    assert counters["codeagent_calls"] == 2
    assert verdict["approved"] is False
    assert verdict["summary"] == "Codex review failed"
    assert verdict["blocking"] == ["codex_call_error: command timed out"]


def test_network_error_retries_once_then_fails(monkeypatch: pytest.MonkeyPatch):
    pr_number = 202
    gh_view_payload = {"title": "Network case", "body": "", "files": [{"path": "n.py"}]}
    gh_diff_text = "diff --git a/n.py b/n.py\n+1\n"

    codeagent_fail_1 = subprocess.CompletedProcess(
        ["codeagent-wrapper", "--backend", "codex", "-", "."],
        1,
        stdout="",
        stderr="network error: connection reset",
    )
    codeagent_fail_2 = subprocess.CompletedProcess(
        ["codeagent-wrapper", "--backend", "codex", "-", "."],
        1,
        stdout="",
        stderr="network error: connection reset",
    )

    counters = _install_fake_subprocess_run(
        monkeypatch,
        pr_number=pr_number,
        gh_view_payload=gh_view_payload,
        gh_diff_text=gh_diff_text,
        codeagent_sequence=[codeagent_fail_1, codeagent_fail_2],
    )
    monkeypatch.setattr(codex_review.time, "sleep", lambda _seconds: None)

    verdict = codex_review.review_pr_with_codex(
        pr_number,
        max_retries=1,
        workdir=".",
        codex_timeout_s=1,
    )
    assert counters["codeagent_calls"] == 2
    assert verdict["approved"] is False
    assert verdict["blocking"] == ["codex_call_error: network error: connection reset"]


def test_confidence_low_logs_warning_but_does_not_block(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    pr_number = 303
    gh_view_payload = {"title": "Low confidence", "body": "", "files": [{"path": "c.py"}]}
    gh_diff_text = "diff --git a/c.py b/c.py\n+1\n"

    verdict_payload = {"approved": True, "blocking": [], "summary": "Looks ok", "confidence": 0.4}
    codeagent_ok = subprocess.CompletedProcess(
        ["codeagent-wrapper", "--backend", "codex", "-", "."],
        0,
        stdout=json.dumps(verdict_payload),
        stderr="",
    )

    _install_fake_subprocess_run(
        monkeypatch,
        pr_number=pr_number,
        gh_view_payload=gh_view_payload,
        gh_diff_text=gh_diff_text,
        codeagent_sequence=[codeagent_ok],
    )

    caplog.set_level(logging.WARNING, logger=codex_review.logger.name)
    verdict = codex_review.review_pr_with_codex(pr_number, max_retries=0, workdir=".")

    assert verdict["approved"] is True
    assert any("Codex confidence below threshold" in record.message for record in caplog.records)


def test_parse_verdict_extracts_embedded_json():
    embedded = (
        "some logs...\n"
        "```json\n"
        '{"approved": true, "blocking": [], "summary": "ok", "confidence": 0.9}\n'
        "```\n"
        "done\n"
    )
    verdict = codex_review.parse_verdict(embedded)
    assert verdict["approved"] is True
    assert verdict["summary"] == "ok"


def test_parse_verdict_missing_keys_raises():
    with pytest.raises(ValueError):
        codex_review.parse_verdict('{"approved": true, "blocking": [], "summary": "ok"}')


def test_run_cmd_handles_filenotfound(monkeypatch: pytest.MonkeyPatch):
    def raise_not_found(*_args, **_kwargs):
        raise FileNotFoundError("nope")

    monkeypatch.setattr(codex_review.subprocess, "run", raise_not_found)
    returncode, stdout, stderr = codex_review._run_cmd(["missing"], timeout_s=1)
    assert returncode == -1
    assert stdout == ""
    assert "command not found" in stderr


def test_run_cmd_handles_generic_exception(monkeypatch: pytest.MonkeyPatch):
    def raise_boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(codex_review.subprocess, "run", raise_boom)
    returncode, stdout, stderr = codex_review._run_cmd(["boom"], timeout_s=1)
    assert returncode == -1
    assert stdout == ""
    assert stderr == "boom"


def test_run_gh_json_returns_none_on_nonzero(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(codex_review, "_run_gh", lambda *_args, **_kwargs: (1, "", "err"))
    assert codex_review._run_gh_json(["gh", "noop"], timeout_s=1) is None


def test_run_gh_json_returns_none_on_invalid_json(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(codex_review, "_run_gh", lambda *_args, **_kwargs: (0, "not-json", ""))
    assert codex_review._run_gh_json(["gh", "noop"], timeout_s=1) is None


def test_review_pr_context_fetch_failure_returns_error(monkeypatch: pytest.MonkeyPatch):
    pr_number = 404

    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="gh error")
        raise AssertionError(f"Unexpected command: {args!r}")

    monkeypatch.setattr(codex_review.subprocess, "run", fake_run)

    verdict = codex_review.review_pr_with_codex(pr_number, max_retries=0, workdir=".")
    assert verdict["approved"] is False
    assert verdict["summary"] == "Failed to fetch PR context"
    assert verdict["blocking"] and verdict["blocking"][0].startswith("pr_context_error:")


def test_fetch_pr_context_repo_flag(monkeypatch: pytest.MonkeyPatch):
    pr_number = 505
    repo = "o/r"

    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "pr", "view"]:
            assert "--repo" in args
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=json.dumps({"title": "t", "body": "b", "files": [{"path": "a.py"}]}),
                stderr="",
            )
        if args[:3] == ["gh", "pr", "diff"]:
            assert "--repo" in args
            return subprocess.CompletedProcess(args, 0, stdout="diff --git a/a b/a\n", stderr="")
        raise AssertionError(f"Unexpected command: {args!r}")

    monkeypatch.setattr(codex_review.subprocess, "run", fake_run)
    ctx = codex_review.fetch_pr_context(pr_number, repo=repo, timeout_s=1)
    assert ctx.pr_number == pr_number
    assert ctx.files == ["a.py"]


def test_extract_json_object_skips_invalid_prefix_brace():
    text = (
        "{not-json}\n"
        '{"approved": true, "blocking": [], "summary": "ok", "confidence": 0.9}\n'
    )
    verdict = codex_review.parse_verdict(text)
    assert verdict["approved"] is True


@pytest.mark.parametrize(
    "payload",
    [
        '{"approved": "yes", "blocking": [], "summary": "ok", "confidence": 0.9}',
        '{"approved": true, "blocking": "x", "summary": "ok", "confidence": 0.9}',
        '{"approved": true, "blocking": [], "summary": 1, "confidence": 0.9}',
        '{"approved": true, "blocking": [], "summary": "ok", "confidence": "x"}',
        '{"approved": true, "blocking": [], "summary": "ok", "confidence": "nan"}',
    ],
)
def test_parse_verdict_type_validation_raises(payload: str):
    with pytest.raises(ValueError):
        codex_review.parse_verdict(payload)


def test_parse_verdict_empty_text_raises():
    with pytest.raises(ValueError):
        codex_review.parse_verdict("")
