#!/usr/bin/env python3
"""
Unit tests for test_runner.py module.

Tests cover:
- parse_test_plan(): Extract test commands from PR body and dev-plan.md
- execute_tests(): Mock subprocess execution, capture stdout/stderr and return code
- report_results(): Update state.test_results, optionally post to PR
- Multiple test framework command format support
- Failure handling and error recording
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from test_runner import (
    TestRunner,
    TestStep,
    TestStepResult,
    TestResults,
    TestStatus,
    parse_dev_plan_tests,
)
from state import StateManager, Phase, AutopilotState


class TestTestStep(unittest.TestCase):
    """Test TestStep dataclass."""

    def test_default_values(self):
        """Test default values are set correctly."""
        step = TestStep(command="pytest tests/")
        self.assertEqual(step.command, "pytest tests/")
        self.assertEqual(step.description, "")
        self.assertEqual(step.expected_output, "")
        self.assertEqual(step.timeout, 300)
        self.assertIsNone(step.working_dir)
        self.assertEqual(step.env, {})

    def test_to_dict(self):
        """Test conversion to dictionary."""
        step = TestStep(
            command="pytest tests/ -v",
            description="Run pytest",
            timeout=600,
            working_dir="/tmp",
            env={"DEBUG": "1"},
        )
        d = step.to_dict()
        self.assertEqual(d["command"], "pytest tests/ -v")
        self.assertEqual(d["description"], "Run pytest")
        self.assertEqual(d["timeout"], 600)
        self.assertEqual(d["working_dir"], "/tmp")
        self.assertEqual(d["env"], {"DEBUG": "1"})

    def test_from_dict(self):
        """Test creation from dictionary."""
        data = {
            "command": "npm test",
            "description": "Run npm tests",
            "timeout": 120,
        }
        step = TestStep.from_dict(data)
        self.assertEqual(step.command, "npm test")
        self.assertEqual(step.description, "Run npm tests")
        self.assertEqual(step.timeout, 120)


class TestTestResults(unittest.TestCase):
    """Test TestResults dataclass."""

    def test_empty_results(self):
        """Test empty results."""
        results = TestResults()
        self.assertEqual(results.total, 0)
        self.assertEqual(results.success_rate, 0.0)
        self.assertTrue(results.all_passed)

    def test_mixed_results(self):
        """Test mixed pass/fail results."""
        results = TestResults(passed=3, failed=1, skipped=1, error=0)
        self.assertEqual(results.total, 5)
        self.assertEqual(results.success_rate, 60.0)
        self.assertFalse(results.all_passed)

    def test_all_passed(self):
        """Test all_passed property."""
        results = TestResults(passed=5, failed=0, skipped=2, error=0)
        self.assertTrue(results.all_passed)

        results_with_error = TestResults(passed=5, failed=0, skipped=0, error=1)
        self.assertFalse(results_with_error.all_passed)

    def test_to_dict(self):
        """Test conversion to dictionary."""
        results = TestResults(
            passed=2,
            failed=1,
            skipped=0,
            error=0,
            total_duration=5.5,
            start_time="2024-01-01T10:00:00",
            end_time="2024-01-01T10:00:05",
        )
        d = results.to_dict()
        self.assertEqual(d["passed"], 2)
        self.assertEqual(d["failed"], 1)
        self.assertEqual(d["total"], 3)
        self.assertAlmostEqual(d["success_rate"], 66.67, delta=0.1)
        self.assertFalse(d["all_passed"])


class TestParseTestPlan(unittest.TestCase):
    """Test parse_test_plan() method."""

    def setUp(self):
        """Set up test fixtures."""
        self.runner = TestRunner()

    def test_parse_checkbox_format(self):
        """Test parsing checkbox format test plan."""
        source = """
## Test Plan
- [ ] pytest tests/ -v
- [ ] npm test
- [x] make lint
"""
        steps = self.runner.parse_test_plan(source)
        self.assertEqual(len(steps), 3)
        self.assertEqual(steps[0].command, "pytest tests/ -v")
        self.assertEqual(steps[1].command, "npm test")
        self.assertEqual(steps[2].command, "make lint")

    def test_parse_checkbox_with_backticks(self):
        """Test parsing checkbox format with backticks."""
        source = """
## Test Plan
- [ ] `pytest tests/ -v`
- [ ] `npm run test`
"""
        steps = self.runner.parse_test_plan(source)
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0].command, "pytest tests/ -v")
        self.assertEqual(steps[1].command, "npm run test")

    def test_parse_test_command_field(self):
        """Test parsing **Test Command** field format."""
        source = """
### Task 1
- **Test Command**: `pytest tests/test_foo.py -v`
"""
        steps = self.runner.parse_test_plan(source)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].command, "pytest tests/test_foo.py -v")

    def test_parse_code_block_in_test_plan_section(self):
        """Test parsing code blocks within Test Plan section."""
        source = """
## Test Plan

Run the following tests:

```bash
pytest tests/ -v
npm test
```

## Other Section
"""
        steps = self.runner.parse_test_plan(source)
        self.assertGreaterEqual(len(steps), 2)
        commands = [s.command for s in steps]
        self.assertIn("pytest tests/ -v", commands)
        self.assertIn("npm test", commands)

    def test_parse_empty_source(self):
        """Test parsing empty source."""
        steps = self.runner.parse_test_plan("")
        # May return auto-detected commands or empty list
        self.assertIsInstance(steps, list)

    def test_parse_no_test_commands(self):
        """Test parsing source with no test commands."""
        source = """
## Description
This is a description without test commands.

## Implementation
Some implementation details.
"""
        steps = self.runner.parse_test_plan(source)
        # Should either return empty or auto-detected
        self.assertIsInstance(steps, list)

    def test_parse_deduplication(self):
        """Test that duplicate commands are deduplicated."""
        source = """
## Test Plan
- [ ] pytest tests/ -v
- [ ] pytest tests/ -v
- [ ] npm test
"""
        steps = self.runner.parse_test_plan(source)
        commands = [s.command for s in steps]
        # Should have unique commands
        self.assertEqual(len(commands), len(set(commands)))

    def test_parse_chinese_test_plan_header(self):
        """Test parsing Chinese 测试计划 header."""
        source = """
## 测试计划
- [ ] pytest tests/ -v
"""
        steps = self.runner.parse_test_plan(source)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].command, "pytest tests/ -v")


class TestAutoDetect(unittest.TestCase):
    """Test auto-detection of test commands."""

    def test_detect_pytest_with_tests_dir(self):
        """Test detecting pytest when tests/ directory exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create pytest marker and tests directory
            Path(tmpdir, "pyproject.toml").touch()
            Path(tmpdir, "tests").mkdir()

            runner = TestRunner(working_dir=tmpdir)
            steps = runner._auto_detect_test_commands()

            self.assertTrue(any("pytest" in s.command for s in steps))

    def test_detect_npm_test(self):
        """Test detecting npm test from package.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create package.json with test script
            package_json = Path(tmpdir, "package.json")
            package_json.write_text(json.dumps({
                "scripts": {"test": "jest"}
            }))

            runner = TestRunner(working_dir=tmpdir)
            steps = runner._auto_detect_test_commands()

            self.assertTrue(any("npm test" in s.command for s in steps))

    def test_detect_make_test(self):
        """Test detecting make test from Makefile."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create Makefile with test target
            makefile = Path(tmpdir, "Makefile")
            makefile.write_text("test:\n\tpytest\n\nlint:\n\tflake8\n")

            runner = TestRunner(working_dir=tmpdir)
            steps = runner._auto_detect_test_commands()

            commands = [s.command for s in steps]
            self.assertIn("make test", commands)
            self.assertIn("make lint", commands)

    def test_detect_no_frameworks(self):
        """Test no detection in empty directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = TestRunner(working_dir=tmpdir)
            steps = runner._auto_detect_test_commands()

            self.assertEqual(len(steps), 0)


class TestExecuteTests(unittest.TestCase):
    """Test execute_tests() method."""

    def setUp(self):
        """Set up test fixtures."""
        self.runner = TestRunner()

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_execute_single_passing_test(self, mock_which, mock_run):
        """Test executing a single passing test."""
        mock_which.return_value = "/usr/bin/echo"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="All tests passed",
            stderr="",
        )

        steps = [TestStep(command="echo test")]
        results = self.runner.execute_tests(steps)

        self.assertEqual(results.passed, 1)
        self.assertEqual(results.failed, 0)
        self.assertTrue(results.all_passed)

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_execute_single_failing_test(self, mock_which, mock_run):
        """Test executing a single failing test."""
        mock_which.return_value = "/usr/bin/pytest"
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="1 failed",
            stderr="AssertionError",
        )

        steps = [TestStep(command="pytest tests/")]
        results = self.runner.execute_tests(steps)

        self.assertEqual(results.passed, 0)
        self.assertEqual(results.failed, 1)
        self.assertFalse(results.all_passed)

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_execute_multiple_tests(self, mock_which, mock_run):
        """Test executing multiple tests."""
        mock_which.return_value = "/usr/bin/test"
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="OK", stderr=""),
            MagicMock(returncode=1, stdout="FAIL", stderr="Error"),
            MagicMock(returncode=0, stdout="OK", stderr=""),
        ]

        steps = [
            TestStep(command="test1"),
            TestStep(command="test2"),
            TestStep(command="test3"),
        ]
        results = self.runner.execute_tests(steps)

        self.assertEqual(results.passed, 2)
        self.assertEqual(results.failed, 1)
        self.assertEqual(results.total, 3)

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_execute_stop_on_failure(self, mock_which, mock_run):
        """Test stop_on_failure option."""
        mock_which.return_value = "/usr/bin/test"
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="OK", stderr=""),
            MagicMock(returncode=1, stdout="FAIL", stderr="Error"),
        ]

        steps = [
            TestStep(command="test1"),
            TestStep(command="test2"),
            TestStep(command="test3"),
        ]
        results = self.runner.execute_tests(steps, stop_on_failure=True)

        self.assertEqual(results.passed, 1)
        self.assertEqual(results.failed, 1)
        self.assertEqual(results.skipped, 1)
        self.assertEqual(results.total, 3)

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_execute_timeout(self, mock_which, mock_run):
        """Test handling timeout."""
        mock_which.return_value = "/usr/bin/test"
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd=["test"],
            timeout=10,
        )

        steps = [TestStep(command="test", timeout=10)]
        results = self.runner.execute_tests(steps)

        self.assertEqual(results.error, 1)
        self.assertIn("Timeout", results.details[0].error_message)

    @patch("shutil.which")
    def test_execute_command_not_found(self, mock_which):
        """Test handling command not found."""
        mock_which.return_value = None

        steps = [TestStep(command="nonexistent_command")]
        results = self.runner.execute_tests(steps)

        self.assertEqual(results.skipped, 1)
        self.assertIn("not found", results.details[0].error_message)

    def test_execute_empty_command(self):
        """Test handling empty command."""
        steps = [TestStep(command="")]
        results = self.runner.execute_tests(steps)

        self.assertEqual(results.error, 1)

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_callbacks_are_called(self, mock_which, mock_run):
        """Test that callbacks are invoked."""
        mock_which.return_value = "/usr/bin/test"
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        start_calls = []
        complete_calls = []

        runner = TestRunner(
            on_step_start=lambda step: start_calls.append(step),
            on_step_complete=lambda result: complete_calls.append(result),
        )

        steps = [TestStep(command="test1"), TestStep(command="test2")]
        runner.execute_tests(steps)

        self.assertEqual(len(start_calls), 2)
        self.assertEqual(len(complete_calls), 2)


class TestReportResults(unittest.TestCase):
    """Test report_results() method."""

    def setUp(self):
        """Set up test fixtures."""
        self.runner = TestRunner()

    def test_generate_report_all_passed(self):
        """Test generating report when all tests pass."""
        results = TestResults(
            passed=3,
            failed=0,
            skipped=0,
            error=0,
            total_duration=1.5,
        )
        report = self.runner.report_results(results)

        self.assertIn("All Passed", report)
        self.assertIn("3", report)
        self.assertIn("100.0%", report)

    def test_generate_report_with_failures(self):
        """Test generating report with failures."""
        step = TestStep(command="pytest tests/")
        step_result = TestStepResult(
            step=step,
            status=TestStatus.FAILED,
            return_code=1,
            stderr="AssertionError: expected True",
            duration=0.5,
        )
        results = TestResults(
            passed=2,
            failed=1,
            skipped=0,
            error=0,
            total_duration=1.5,
            details=[step_result],
        )
        report = self.runner.report_results(results)

        self.assertIn("Some Failed", report)
        self.assertIn("pytest tests/", report)

    def test_update_state(self):
        """Test updating state manager with results."""
        # Create mock state manager
        mock_state = MagicMock()
        mock_state.state = MagicMock()
        mock_state.state.test_results = []

        results = TestResults(passed=1, failed=0)
        self.runner.report_results(results, state_manager=mock_state)

        self.assertEqual(len(mock_state.state.test_results), 1)

    @patch("subprocess.run")
    def test_post_to_pr(self, mock_run):
        """Test posting results to PR comment."""
        mock_run.return_value = MagicMock(returncode=0)

        results = TestResults(passed=1, failed=0)
        self.runner.report_results(results, pr_number=123)

        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        self.assertIn("gh", call_args)
        self.assertIn("pr", call_args)
        self.assertIn("comment", call_args)
        self.assertIn("123", call_args)


class TestParseDevPlanTests(unittest.TestCase):
    """Test parse_dev_plan_tests() function."""

    def test_parse_existing_file(self):
        """Test parsing existing dev-plan.md file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("""
# Dev Plan

## Test Plan
- [ ] pytest tests/ -v --cov
- [ ] npm test

## Implementation
...
""")
            f.flush()

            try:
                steps = parse_dev_plan_tests(f.name)
                self.assertEqual(len(steps), 2)
            finally:
                os.unlink(f.name)

    def test_parse_nonexistent_file(self):
        """Test parsing nonexistent file returns empty list."""
        steps = parse_dev_plan_tests("/nonexistent/path/dev-plan.md")
        self.assertEqual(steps, [])


class TestIntegrationWithState(unittest.TestCase):
    """Test integration with state.py."""

    def test_state_has_test_results_field(self):
        """Verify AutopilotState has test_results field."""
        state = AutopilotState()
        self.assertTrue(hasattr(state, "test_results"))
        self.assertIsInstance(state.test_results, list)

    def test_phase_includes_test_run(self):
        """Verify Phase enum includes TEST_RUN."""
        self.assertTrue(hasattr(Phase, "TEST_RUN"))
        self.assertEqual(Phase.TEST_RUN.value, "test_run")

    def test_phase_order_includes_test_run(self):
        """Verify phase order includes TEST_RUN after IMPLEMENT."""
        order = Phase.get_phase_order()
        implement_idx = order.index(Phase.IMPLEMENT)
        test_run_idx = order.index(Phase.TEST_RUN)
        pr_review_idx = order.index(Phase.PR_REVIEW)

        self.assertEqual(test_run_idx, implement_idx + 1)
        self.assertEqual(pr_review_idx, test_run_idx + 1)


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and error handling."""

    def setUp(self):
        """Set up test fixtures."""
        self.runner = TestRunner()

    def test_parse_malformed_checkbox(self):
        """Test parsing malformed checkbox format."""
        source = """
- [] pytest  # missing space
-[ ] npm test  # wrong format
- [ ]   # empty
"""
        steps = self.runner.parse_test_plan(source)
        # Should handle gracefully
        self.assertIsInstance(steps, list)

    def test_parse_special_characters_in_command(self):
        """Test parsing commands with special characters."""
        source = """
## Test Plan
- [ ] pytest tests/ -v --cov=src --cov-report=term-missing
- [ ] npm run test:unit -- --coverage
"""
        steps = self.runner.parse_test_plan(source)
        self.assertGreaterEqual(len(steps), 1)

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_capture_long_output(self, mock_which, mock_run):
        """Test capturing and truncating long output."""
        mock_which.return_value = "/usr/bin/test"
        long_output = "x" * 20000
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=long_output,
            stderr="",
        )

        steps = [TestStep(command="test")]
        results = self.runner.execute_tests(steps)

        # Output should be truncated in to_dict
        detail_dict = results.details[0].to_dict()
        self.assertLessEqual(len(detail_dict["stdout"]), 10000)

    def test_is_test_command(self):
        """Test _is_test_command helper."""
        self.assertTrue(self.runner._is_test_command("pytest tests/"))
        self.assertTrue(self.runner._is_test_command("npm test"))
        self.assertTrue(self.runner._is_test_command("make test"))
        self.assertTrue(self.runner._is_test_command("cargo test"))
        self.assertTrue(self.runner._is_test_command("go test ./..."))
        self.assertTrue(self.runner._is_test_command("yarn test"))
        self.assertTrue(self.runner._is_test_command("make lint"))

        self.assertFalse(self.runner._is_test_command("echo hello"))
        self.assertFalse(self.runner._is_test_command("cat file.txt"))


if __name__ == "__main__":
    unittest.main()
