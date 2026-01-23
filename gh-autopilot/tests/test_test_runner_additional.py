#!/usr/bin/env python3
"""
test_runner.py 补充分支覆盖测试。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# 添加 scripts 目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from test_runner import (  # noqa: E402
    TestResults,
    TestRunner,
    TestStatus,
    TestStep,
    TestStepResult,
)


def test_test_results_from_dict_smoke():
    data = {
        "passed": 1,
        "failed": 2,
        "skipped": 3,
        "error": 4,
        "total_duration": 1.5,
        "start_time": "s",
        "end_time": "e",
    }
    results = TestResults.from_dict(data)
    assert results.passed == 1
    assert results.failed == 2
    assert results.details == []


@pytest.mark.parametrize(
    ("cmd", "expected"),
    [
        ("make test", "Run make tests"),
        ("cargo test", "Run Rust tests"),
        ("go test ./...", "Run Go tests"),
    ],
)
def test_extract_description_branches(tmp_path, cmd, expected):
    runner = TestRunner(working_dir=str(tmp_path))
    assert runner._extract_description(cmd) == expected


def test_auto_detect_pytest_without_tests_dir(tmp_path):
    (tmp_path / "pytest.ini").write_text("", encoding="utf-8")
    runner = TestRunner(working_dir=str(tmp_path))
    steps = runner._auto_detect_test_commands()
    assert any(s.command == "pytest -v" for s in steps)


def test_auto_detect_invalid_package_json_does_not_crash(tmp_path):
    (tmp_path / "package.json").write_text("{not json", encoding="utf-8")
    runner = TestRunner(working_dir=str(tmp_path))
    steps = runner._auto_detect_test_commands()
    # 解析失败应被吞掉，不会添加 npm test
    assert not any(s.command == "npm test" for s in steps)


def test_auto_detect_makefile_read_error_is_ignored(tmp_path):
    (tmp_path / "Makefile").write_text("test:\n\techo ok\n", encoding="utf-8")
    runner = TestRunner(working_dir=str(tmp_path))

    with patch("pathlib.Path.read_text", side_effect=IOError("nope")):
        steps = runner._auto_detect_test_commands()
    assert steps == []


def test_execute_single_step_generic_exception(tmp_path):
    runner = TestRunner(working_dir=str(tmp_path))
    step = TestStep(command="pytest -v")

    with patch("subprocess.run", side_effect=RuntimeError("boom")):
        result = runner._execute_single_step(step)

    assert result.status == TestStatus.ERROR
    assert "boom" in (result.error_message or "")


def test_generate_report_includes_error_message_and_stderr_preview(tmp_path):
    runner = TestRunner(working_dir=str(tmp_path))

    step = TestStep(command="pytest -v")
    detail = TestStepResult(
        step=step,
        status=TestStatus.FAILED,
        return_code=1,
        stdout="",
        stderr="line1\nline2\n",
        duration=0.1,
        timestamp="t",
        error_message="failed",
    )
    results = TestResults(passed=0, failed=1, total_duration=0.1, details=[detail])

    report = runner._generate_report(results)
    assert "Error: failed" in report
    assert "line1" in report


def test_post_to_pr_exception_returns_false(tmp_path):
    runner = TestRunner(working_dir=str(tmp_path))
    with patch("subprocess.run", side_effect=RuntimeError("boom")):
        assert runner._post_to_pr(1, "x") is False

