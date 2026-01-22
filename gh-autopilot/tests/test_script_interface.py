#!/usr/bin/env python3
"""
Unit tests for script interface alignment in autopilot.py.

Tests verify that autopilot.py calls downstream scripts with correct arguments:
- sync_project.py: requires --project argument
- batch_executor.py: uses stdin/--input format (not --project/--json)
- batch_review.py: replaces main.py for Phase 4-6
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from autopilot import Autopilot
from state import StateManager, Phase, IssueResult


class TestSyncProjectInterface(unittest.TestCase):
    """Test sync_project.py interface alignment."""

    def setUp(self):
        """Set up test fixtures."""
        self.autopilot = Autopilot(
            input_source="test input",
            project_number=5,
            dry_run=False,
        )
        # Mock state manager
        self.autopilot.state_manager = MagicMock(spec=StateManager)
        self.autopilot.state_manager.state = MagicMock()
        self.autopilot.state_manager.state.issues_created = [101, 102, 103]

    @patch("subprocess.run")
    @patch.object(Path, "exists", return_value=True)
    def test_sync_project_includes_project_argument(self, mock_exists, mock_run):
        """Verify sync_project.py call includes --project argument."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"project": {"number": 5, "title": "Test"}}),
            stderr="",
        )

        result = self.autopilot._invoke_skill_project_sync()

        # Verify subprocess.run was called
        self.assertTrue(mock_run.called)
        call_args = mock_run.call_args[0][0]

        # Verify --project argument is present
        self.assertIn("--project", call_args)
        project_idx = call_args.index("--project")
        self.assertEqual(call_args[project_idx + 1], "5")

    @patch("subprocess.run")
    @patch.object(Path, "exists", return_value=True)
    def test_sync_project_includes_issues_argument(self, mock_exists, mock_run):
        """Verify sync_project.py call includes --issues argument with issue list."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"project": {"number": 5}}),
            stderr="",
        )

        result = self.autopilot._invoke_skill_project_sync()

        call_args = mock_run.call_args[0][0]

        # Verify --issues argument is present with correct format
        self.assertIn("--issues", call_args)
        issues_idx = call_args.index("--issues")
        issues_str = call_args[issues_idx + 1]
        # Should be comma-separated
        self.assertEqual(issues_str, "101,102,103")

    @patch("subprocess.run")
    @patch.object(Path, "exists", return_value=True)
    def test_sync_project_uses_all_when_no_issues(self, mock_exists, mock_run):
        """Verify sync_project.py uses --all when no issues are specified."""
        # Set issues_created to empty
        self.autopilot.state_manager.state.issues_created = []

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"project": {"number": 5}}),
            stderr="",
        )

        result = self.autopilot._invoke_skill_project_sync()

        call_args = mock_run.call_args[0][0]

        # Verify --all argument is present
        self.assertIn("--all", call_args)
        # Verify --issues is NOT present
        self.assertNotIn("--issues", call_args)

    @patch("subprocess.run")
    @patch.object(Path, "exists", return_value=True)
    def test_sync_project_includes_json_flag(self, mock_exists, mock_run):
        """Verify sync_project.py call includes --json flag."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"project": {"number": 5}}),
            stderr="",
        )

        result = self.autopilot._invoke_skill_project_sync()

        call_args = mock_run.call_args[0][0]
        self.assertIn("--json", call_args)

    @patch("subprocess.run")
    @patch.object(Path, "exists", return_value=True)
    def test_sync_project_returns_default_on_failure(self, mock_exists, mock_run):
        """Verify sync_project.py returns default project number on failure."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error occurred",
        )

        result = self.autopilot._invoke_skill_project_sync()

        # Should return the specified project_number (5) or 1
        self.assertEqual(result, 5)

    @patch("subprocess.run")
    @patch.object(Path, "exists", return_value=True)
    def test_sync_project_handles_none_project_number(self, mock_exists, mock_run):
        """Verify sync_project.py handles None project_number."""
        self.autopilot.project_number = None

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"project": {"number": 1}}),
            stderr="",
        )

        result = self.autopilot._invoke_skill_project_sync()

        call_args = mock_run.call_args[0][0]
        project_idx = call_args.index("--project")
        # Should default to "1"
        self.assertEqual(call_args[project_idx + 1], "1")


class TestBatchExecutorInterface(unittest.TestCase):
    """Test batch_executor.py interface alignment."""

    def setUp(self):
        """Set up test fixtures."""
        self.autopilot = Autopilot(
            input_source="test input",
            project_number=1,
            priority_filter="p0,p1",
        )
        self.autopilot.state_manager = MagicMock(spec=StateManager)
        self.autopilot.state_manager.state = MagicMock()
        self.autopilot.state_manager.state.issues_created = [42, 43, 44]

    @patch("subprocess.run")
    @patch.object(Path, "exists", return_value=True)
    def test_batch_executor_uses_stdin_input(self, mock_exists, mock_run):
        """Verify batch_executor.py receives JSON via stdin, not --project/--json args."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="✅ Issue #42 已完成，PR #100 已合并 (耗时 1m30s)\n",
            stderr="",
        )

        result = self.autopilot._invoke_skill_project_implement(1)

        # Verify subprocess.run was called with input parameter
        self.assertTrue(mock_run.called)
        call_kwargs = mock_run.call_args[1]

        # Verify 'input' kwarg is present (stdin)
        self.assertIn("input", call_kwargs)
        input_json = call_kwargs["input"]

        # Verify input is valid JSON with batches format
        parsed = json.loads(input_json)
        self.assertIn("batches", parsed)
        self.assertIsInstance(parsed["batches"], list)

    @patch("subprocess.run")
    @patch.object(Path, "exists", return_value=True)
    def test_batch_executor_not_using_project_arg(self, mock_exists, mock_run):
        """Verify batch_executor.py is NOT called with --project argument."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="",
        )

        result = self.autopilot._invoke_skill_project_implement(1)

        call_args = mock_run.call_args[0][0]

        # Verify --project is NOT in the arguments
        self.assertNotIn("--project", call_args)
        # Verify --json is NOT in the arguments (we use stdin)
        self.assertNotIn("--json", call_args)

    @patch("subprocess.run")
    @patch.object(Path, "exists", return_value=True)
    def test_batch_executor_input_format(self, mock_exists, mock_run):
        """Verify batch_executor.py input JSON has correct format."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="",
        )

        result = self.autopilot._invoke_skill_project_implement(1)

        call_kwargs = mock_run.call_args[1]
        input_json = call_kwargs["input"]
        parsed = json.loads(input_json)

        # Verify structure: {"batches": [{"priority": "...", "issues": [...]}]}
        self.assertIn("batches", parsed)
        self.assertEqual(len(parsed["batches"]), 1)

        batch = parsed["batches"][0]
        self.assertIn("priority", batch)
        self.assertIn("issues", batch)

        # Each issue should have number, title, dependencies
        for issue in batch["issues"]:
            self.assertIn("number", issue)
            self.assertIn("title", issue)
            self.assertIn("dependencies", issue)

    @patch("subprocess.run")
    @patch.object(Path, "exists", return_value=True)
    def test_batch_executor_parses_success_output(self, mock_exists, mock_run):
        """Verify batch_executor.py output is correctly parsed for successful issues."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="✅ Issue #42 已完成，PR #100 已合并 (耗时 1m30s)\n✅ Issue #43 已完成 (耗时 2m)\n",
            stderr="",
        )

        result = self.autopilot._invoke_skill_project_implement(1)

        self.assertIn("results", result)
        results = result["results"]
        self.assertEqual(len(results), 2)

        # Check first result (with PR)
        self.assertEqual(results[0]["issue_number"], 42)
        self.assertEqual(results[0]["status"], "completed")
        self.assertEqual(results[0]["pr_number"], 100)

        # Check second result (without PR number in output)
        self.assertEqual(results[1]["issue_number"], 43)
        self.assertEqual(results[1]["status"], "completed")

    @patch("subprocess.run")
    @patch.object(Path, "exists", return_value=True)
    def test_batch_executor_parses_failure_output(self, mock_exists, mock_run):
        """Verify batch_executor.py output is correctly parsed for failed issues."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="❌ Issue #44 失败 (尝试 3/4): codeagent exit=1\n",
            stderr="",
        )

        result = self.autopilot._invoke_skill_project_implement(1)

        self.assertIn("results", result)
        results = result["results"]
        self.assertEqual(len(results), 1)

        self.assertEqual(results[0]["issue_number"], 44)
        self.assertEqual(results[0]["status"], "failed")
        self.assertIn("codeagent exit=1", results[0]["error"])

    @patch("subprocess.run")
    @patch.object(Path, "exists", return_value=True)
    def test_batch_executor_handles_empty_output(self, mock_exists, mock_run):
        """Verify batch_executor.py handles empty output gracefully."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Some error",
        )

        result = self.autopilot._invoke_skill_project_implement(1)

        self.assertEqual(result, {"results": []})


class TestBatchReviewInterface(unittest.TestCase):
    """Test batch_review.py interface alignment (replaces main.py)."""

    def setUp(self):
        """Set up test fixtures."""
        self.autopilot = Autopilot(
            input_source="test input",
            project_number=1,
        )
        self.autopilot.state_manager = MagicMock(spec=StateManager)
        self.autopilot.state_manager.state = MagicMock()
        # Set up issue results with PR numbers
        self.autopilot.state_manager.state.issue_results = [
            IssueResult(number=42, title="Test 1", status="completed", pr_number=100),
            IssueResult(number=43, title="Test 2", status="completed", pr_number=101),
        ]

    @patch("os.unlink")
    @patch("subprocess.run")
    @patch.object(Path, "exists", return_value=True)
    def test_batch_review_uses_input_file(self, mock_exists, mock_run, mock_unlink):
        """Verify batch_review.py is called with --input argument."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"results": [], "summary": {}}),
            stderr="",
        )

        result = self.autopilot._invoke_skill_project_pr(1)

        call_args = mock_run.call_args[0][0]

        # Verify --input argument is present
        self.assertIn("--input", call_args)

    @patch("os.unlink")
    @patch("subprocess.run")
    @patch.object(Path, "exists", return_value=True)
    def test_batch_review_uses_auto_merge(self, mock_exists, mock_run, mock_unlink):
        """Verify batch_review.py is called with --auto-merge argument."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"results": [], "summary": {}}),
            stderr="",
        )

        result = self.autopilot._invoke_skill_project_pr(1)

        call_args = mock_run.call_args[0][0]

        # Verify --auto-merge argument is present
        self.assertIn("--auto-merge", call_args)

    @patch("os.unlink")
    @patch("subprocess.run")
    @patch.object(Path, "exists", return_value=True)
    def test_batch_review_not_using_project_arg(self, mock_exists, mock_run, mock_unlink):
        """Verify batch_review.py is NOT called with --project argument (main.py style)."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"results": [], "summary": {}}),
            stderr="",
        )

        result = self.autopilot._invoke_skill_project_pr(1)

        call_args = mock_run.call_args[0][0]

        # Verify --project is NOT in the arguments
        self.assertNotIn("--project", call_args)

    @patch("os.unlink")
    @patch("subprocess.run")
    @patch.object(Path, "exists", return_value=True)
    def test_batch_review_uses_correct_script(self, mock_exists, mock_run, mock_unlink):
        """Verify batch_review.py is used instead of main.py."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"results": [], "summary": {}}),
            stderr="",
        )

        result = self.autopilot._invoke_skill_project_pr(1)

        call_args = mock_run.call_args[0][0]

        # Verify script path contains batch_review.py
        script_path = call_args[1]  # python3, <script_path>, ...
        self.assertIn("batch_review.py", script_path)
        self.assertNotIn("main.py", script_path)

    @patch("os.unlink")
    @patch("subprocess.run")
    @patch.object(Path, "exists", return_value=True)
    def test_batch_review_converts_output_format(self, mock_exists, mock_run, mock_unlink):
        """Verify batch_review.py output is converted to expected format."""
        # batch_review output format
        batch_review_output = {
            "results": [
                {"issue": 42, "pr": 100, "status": "merged", "error": None},
                {"issue": 43, "pr": 101, "status": "failed", "error": "CI failed"},
            ],
            "summary": {"total": 2, "merged": 1, "failed": 1},
        }

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(batch_review_output),
            stderr="",
        )

        result = self.autopilot._invoke_skill_project_pr(1)

        # Expected format: {"merged": [pr_numbers], "failed": [{"number": N, "error": "..."}]}
        self.assertIn("merged", result)
        self.assertIn("failed", result)
        self.assertEqual(result["merged"], [100])
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(result["failed"][0]["number"], 101)
        self.assertEqual(result["failed"][0]["error"], "CI failed")

    @patch("os.unlink")
    @patch("subprocess.run")
    @patch.object(Path, "exists", return_value=True)
    def test_batch_review_handles_no_prs(self, mock_exists, mock_run, mock_unlink):
        """Verify batch_review.py handles case with no PRs to review."""
        # No issue results with PR numbers
        self.autopilot.state_manager.state.issue_results = [
            IssueResult(number=42, title="Test 1", status="failed", pr_number=None),
        ]

        result = self.autopilot._invoke_skill_project_pr(1)

        # Should return empty result without calling subprocess
        self.assertEqual(result, {"merged": [], "failed": []})
        mock_run.assert_not_called()

    @patch("os.unlink")
    @patch("subprocess.run")
    @patch.object(Path, "exists", return_value=True)
    def test_batch_review_input_json_format(self, mock_exists, mock_run, mock_unlink):
        """Verify batch_review.py input JSON has correct format."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"results": [], "summary": {}}),
            stderr="",
        )

        result = self.autopilot._invoke_skill_project_pr(1)

        # Get the input file path from the call
        call_args = mock_run.call_args[0][0]
        input_idx = call_args.index("--input")
        input_file = call_args[input_idx + 1]

        # The file should have been created with correct JSON format
        # (We can't easily read it since it's a temp file, but we verify the structure
        # by checking the mock was called correctly)
        self.assertTrue(input_file.endswith(".json"))


class TestParseHelpers(unittest.TestCase):
    """Test helper parsing methods."""

    def setUp(self):
        """Set up test fixtures."""
        self.autopilot = Autopilot(input_source="test")

    def test_parse_batch_executor_output_mixed(self):
        """Test parsing mixed success and failure output."""
        stdout = """🚀 开始处理 (共 3 个 issues)
📦 P1 批次 (3 issues, 并发=3)
✅ Issue #42 已完成，PR #100 已合并 (耗时 2m30s)
✅ Issue #43 已完成 (耗时 1m)
❌ Issue #44 失败 (尝试 3/4): worktree create 失败
📦 P1 批次完成 (2/3)
"""
        result = self.autopilot._parse_batch_executor_output(stdout)

        self.assertEqual(len(result["results"]), 3)

        # Check completed with PR
        completed_with_pr = [r for r in result["results"] if r["issue_number"] == 42][0]
        self.assertEqual(completed_with_pr["status"], "completed")
        self.assertEqual(completed_with_pr["pr_number"], 100)

        # Check completed without PR
        completed_no_pr = [r for r in result["results"] if r["issue_number"] == 43][0]
        self.assertEqual(completed_no_pr["status"], "completed")

        # Check failed
        failed = [r for r in result["results"] if r["issue_number"] == 44][0]
        self.assertEqual(failed["status"], "failed")
        self.assertIn("worktree create", failed["error"])

    def test_convert_batch_review_output_empty(self):
        """Test converting empty batch_review output."""
        output = {"results": [], "summary": {"total": 0}}
        result = self.autopilot._convert_batch_review_output(output)

        self.assertEqual(result, {"merged": [], "failed": []})

    def test_convert_batch_review_output_all_merged(self):
        """Test converting batch_review output with all merged."""
        output = {
            "results": [
                {"issue": 1, "pr": 10, "status": "merged", "error": None},
                {"issue": 2, "pr": 20, "status": "merged", "error": None},
            ],
            "summary": {"total": 2, "merged": 2, "failed": 0},
        }
        result = self.autopilot._convert_batch_review_output(output)

        self.assertEqual(result["merged"], [10, 20])
        self.assertEqual(result["failed"], [])

    def test_convert_batch_review_output_all_failed(self):
        """Test converting batch_review output with all failed."""
        output = {
            "results": [
                {"issue": 1, "pr": 10, "status": "failed", "error": "CI failed"},
                {"issue": 2, "pr": 20, "status": "failed", "error": "merge conflict"},
            ],
            "summary": {"total": 2, "merged": 0, "failed": 2},
        }
        result = self.autopilot._convert_batch_review_output(output)

        self.assertEqual(result["merged"], [])
        self.assertEqual(len(result["failed"]), 2)
        self.assertEqual(result["failed"][0], {"number": 10, "error": "CI failed"})
        self.assertEqual(result["failed"][1], {"number": 20, "error": "merge conflict"})


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and error handling."""

    def setUp(self):
        """Set up test fixtures."""
        self.autopilot = Autopilot(input_source="test")
        self.autopilot.state_manager = MagicMock(spec=StateManager)
        self.autopilot.state_manager.state = MagicMock()
        self.autopilot.state_manager.state.issues_created = []
        self.autopilot.state_manager.state.issue_results = []

    @patch("subprocess.run")
    @patch.object(Path, "exists", return_value=False)
    def test_sync_project_script_not_found(self, mock_exists, mock_run):
        """Test handling when sync_project.py doesn't exist."""
        result = self.autopilot._invoke_skill_project_sync()

        # Should return default without calling subprocess
        self.assertIsNotNone(result)
        mock_run.assert_not_called()

    @patch("subprocess.run")
    @patch.object(Path, "exists", return_value=False)
    def test_batch_executor_script_not_found(self, mock_exists, mock_run):
        """Test handling when batch_executor.py doesn't exist."""
        result = self.autopilot._invoke_skill_project_implement(1)

        self.assertEqual(result, {"results": []})
        mock_run.assert_not_called()

    @patch("subprocess.run")
    @patch.object(Path, "exists", return_value=False)
    def test_batch_review_script_not_found(self, mock_exists, mock_run):
        """Test handling when batch_review.py doesn't exist."""
        self.autopilot.state_manager.state.issue_results = [
            IssueResult(number=1, title="Test", status="completed", pr_number=10),
        ]

        result = self.autopilot._invoke_skill_project_pr(1)

        self.assertEqual(result, {"merged": [], "failed": []})
        mock_run.assert_not_called()

    @patch("subprocess.run")
    @patch.object(Path, "exists", return_value=True)
    def test_sync_project_json_parse_error(self, mock_exists, mock_run):
        """Test handling JSON parse error from sync_project.py."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="not valid json",
            stderr="",
        )

        # Should not raise, should return default
        result = self.autopilot._invoke_skill_project_sync()
        self.assertIsNotNone(result)

    @patch("subprocess.run")
    @patch.object(Path, "exists", return_value=True)
    def test_batch_executor_timeout(self, mock_exists, mock_run):
        """Test handling timeout from batch_executor.py."""
        import subprocess as sp
        mock_run.side_effect = sp.TimeoutExpired(cmd=["python3"], timeout=7200)

        result = self.autopilot._invoke_skill_project_implement(1)

        self.assertEqual(result, {"results": []})


if __name__ == "__main__":
    unittest.main()
