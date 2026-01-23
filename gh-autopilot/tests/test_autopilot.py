#!/usr/bin/env python3
"""
gh-autopilot 单元测试。

运行测试:
    cd gh-autopilot/scripts
    python3 -m pytest test_autopilot.py -v --cov=. --cov-report=term-missing
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# 添加 scripts 目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from state import (
    StateManager,
    AutopilotState,
    Phase,
    IssueResult,
    get_state_manager,
)
from report import (
    ReportGenerator,
    ReportConfig,
    generate_report,
)
from autopilot import (
    Autopilot,
    AutopilotError,
)


class TestPhaseEnum(unittest.TestCase):
    """Phase 枚举测试"""

    def test_phase_values(self):
        """测试所有阶段值"""
        self.assertEqual(Phase.INIT.value, "init")
        self.assertEqual(Phase.PRD.value, "prd")
        self.assertEqual(Phase.CREATE_ISSUE.value, "create_issue")
        self.assertEqual(Phase.PROJECT_SYNC.value, "project_sync")
        self.assertEqual(Phase.IMPLEMENT.value, "implement")
        self.assertEqual(Phase.PR_REVIEW.value, "pr_review")
        self.assertEqual(Phase.COMPLETED.value, "completed")
        self.assertEqual(Phase.FAILED.value, "failed")


class TestIssueResult(unittest.TestCase):
    """IssueResult 数据类测试"""

    def test_create_success_result(self):
        """测试创建成功结果"""
        result = IssueResult(
            number=1,
            title="Test Issue",
            status="success",
            pr_number=10,
        )
        self.assertEqual(result.number, 1)
        self.assertEqual(result.title, "Test Issue")
        self.assertEqual(result.status, "success")
        self.assertEqual(result.pr_number, 10)
        self.assertIsNone(result.error)

    def test_create_failed_result(self):
        """测试创建失败结果"""
        result = IssueResult(
            number=2,
            title="Failed Issue",
            status="failed",
            error="API Error",
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error, "API Error")
        self.assertIsNone(result.pr_number)


class TestAutopilotState(unittest.TestCase):
    """AutopilotState 数据类测试"""

    def test_default_values(self):
        """测试默认值"""
        state = AutopilotState()
        self.assertEqual(state.run_id, "")
        self.assertEqual(state.current_phase, Phase.INIT.value)
        self.assertEqual(state.issues_created, [])
        self.assertEqual(state.total_issues, 0)
        self.assertEqual(state.success_count, 0)

    def test_custom_values(self):
        """测试自定义值"""
        state = AutopilotState(
            run_id="test_run",
            input_source="test.md",
            total_issues=5,
        )
        self.assertEqual(state.run_id, "test_run")
        self.assertEqual(state.input_source, "test.md")
        self.assertEqual(state.total_issues, 5)


class TestStateManager(unittest.TestCase):
    """StateManager 测试"""

    def setUp(self):
        """测试前准备"""
        self.temp_dir = tempfile.mkdtemp()
        self.state_path = os.path.join(self.temp_dir, "test-state.json")
        self.manager = StateManager(self.state_path)

    def tearDown(self):
        """测试后清理"""
        if os.path.exists(self.state_path):
            os.remove(self.state_path)
        os.rmdir(self.temp_dir)

    def test_init_state(self):
        """测试初始化状态"""
        state = self.manager.init_state("test-input")
        self.assertNotEqual(state.run_id, "")
        self.assertEqual(state.input_source, "test-input")
        self.assertEqual(state.current_phase, Phase.INIT.value)
        self.assertTrue(os.path.exists(self.state_path))

    def test_load_state(self):
        """测试加载状态"""
        # 先初始化
        self.manager.init_state("test-input")
        self.manager.update_phase(Phase.CREATE_ISSUE)

        # 新建管理器加载
        manager2 = StateManager(self.state_path)
        state = manager2.load_state()

        self.assertIsNotNone(state)
        self.assertEqual(state.input_source, "test-input")
        self.assertEqual(state.current_phase, Phase.CREATE_ISSUE.value)

    def test_load_state_not_exists(self):
        """测试加载不存在的状态"""
        manager = StateManager("/tmp/nonexistent-state.json")
        state = manager.load_state()
        self.assertIsNone(state)

    def test_update_phase(self):
        """测试更新阶段"""
        self.manager.init_state("test")
        self.manager.update_phase(Phase.IMPLEMENT)
        self.assertEqual(self.manager.state.current_phase, Phase.IMPLEMENT.value)

    def test_set_prd_info(self):
        """测试设置 PRD 信息"""
        self.manager.init_state("test")
        self.manager.set_prd_info("/path/to/prd.md", "Test Feature")
        self.assertEqual(self.manager.state.prd_path, "/path/to/prd.md")
        self.assertEqual(self.manager.state.prd_title, "Test Feature")

    def test_set_issues(self):
        """测试设置 Issue"""
        self.manager.init_state("test")
        self.manager.set_issues([1, 2, 3], epic_number=1)
        self.assertEqual(self.manager.state.issues_created, [1, 2, 3])
        self.assertEqual(self.manager.state.epic_number, 1)
        self.assertEqual(self.manager.state.total_issues, 3)

    def test_set_project(self):
        """测试设置 Project"""
        self.manager.init_state("test")
        self.manager.set_project(5, "https://github.com/org/repo/projects/5")
        self.assertEqual(self.manager.state.project_number, 5)
        self.assertEqual(self.manager.state.project_url, "https://github.com/org/repo/projects/5")

    def test_add_issue_result_success(self):
        """测试添加成功的 Issue 结果"""
        self.manager.init_state("test")
        result = IssueResult(number=1, title="Test", status="success", pr_number=10)
        self.manager.add_issue_result(result)

        self.assertEqual(len(self.manager.state.issue_results), 1)
        self.assertEqual(self.manager.state.success_count, 1)
        self.assertEqual(self.manager.state.failed_count, 0)

    def test_add_issue_result_failed(self):
        """测试添加失败的 Issue 结果"""
        self.manager.init_state("test")
        result = IssueResult(number=1, title="Test", status="failed", error="Error")
        self.manager.add_issue_result(result)

        self.assertEqual(self.manager.state.failed_count, 1)
        self.assertEqual(self.manager.state.success_count, 0)

    def test_add_issue_result_skipped(self):
        """测试添加跳过的 Issue 结果"""
        self.manager.init_state("test")
        result = IssueResult(number=1, title="Test", status="skipped")
        self.manager.add_issue_result(result)

        self.assertEqual(self.manager.state.skipped_count, 1)

    def test_add_pr_result(self):
        """测试添加 PR 结果"""
        self.manager.init_state("test")
        self.manager.add_pr_result(10, "merged")
        self.manager.add_pr_result(11, "failed", "CI failed")

        self.assertEqual(len(self.manager.state.pr_results), 2)
        self.assertEqual(self.manager.state.pr_results[0]["status"], "merged")
        self.assertEqual(self.manager.state.pr_results[1]["error"], "CI failed")

    def test_set_error(self):
        """测试记录错误"""
        self.manager.init_state("test")
        self.manager.set_error("Test error")
        self.assertEqual(self.manager.state.last_error, "Test error")
        self.assertEqual(self.manager.state.retry_count, 1)

    def test_can_retry(self):
        """测试重试检查"""
        self.manager.init_state("test")
        self.assertTrue(self.manager.can_retry())

        for _ in range(3):
            self.manager.set_error("Error")

        self.assertFalse(self.manager.can_retry())

    def test_complete_success(self):
        """测试成功完成"""
        self.manager.init_state("test")
        self.manager.complete(success=True)

        self.assertEqual(self.manager.state.current_phase, Phase.COMPLETED.value)
        self.assertNotEqual(self.manager.state.end_time, "")

    def test_complete_failure(self):
        """测试失败完成"""
        self.manager.init_state("test")
        self.manager.complete(success=False)

        self.assertEqual(self.manager.state.current_phase, Phase.FAILED.value)

    def test_get_summary(self):
        """测试获取摘要"""
        self.manager.init_state("test-input")
        self.manager.set_issues([1, 2, 3])
        self.manager.set_project(5)
        self.manager.state.success_count = 2
        self.manager.state.failed_count = 1

        summary = self.manager.get_summary()

        self.assertEqual(summary["input"], "test-input")
        self.assertEqual(summary["total_issues"], 3)
        self.assertEqual(summary["success"], 2)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["project_number"], 5)

    def test_clear(self):
        """测试清除状态"""
        self.manager.init_state("test")
        self.assertTrue(os.path.exists(self.state_path))

        self.manager.clear()
        self.assertFalse(os.path.exists(self.state_path))
        self.assertEqual(self.manager.state.run_id, "")


class TestGetStateManager(unittest.TestCase):
    """get_state_manager 测试"""

    def test_get_default_manager(self):
        """测试获取默认管理器"""
        manager = get_state_manager()
        self.assertIsInstance(manager, StateManager)
        self.assertEqual(str(manager.state_path), StateManager.DEFAULT_STATE_PATH)

    def test_get_custom_manager(self):
        """测试获取自定义路径管理器"""
        manager = get_state_manager("/tmp/custom-state.json")
        self.assertEqual(str(manager.state_path), "/tmp/custom-state.json")


class TestReportConfig(unittest.TestCase):
    """ReportConfig 测试"""

    def test_default_config(self):
        """测试默认配置"""
        config = ReportConfig()
        self.assertTrue(config.show_details)
        self.assertTrue(config.show_failures)
        self.assertEqual(config.format, "text")

    def test_custom_config(self):
        """测试自定义配置"""
        config = ReportConfig(show_details=False, format="json")
        self.assertFalse(config.show_details)
        self.assertEqual(config.format, "json")


class TestReportGenerator(unittest.TestCase):
    """ReportGenerator 测试"""

    def setUp(self):
        """测试前准备"""
        self.state = AutopilotState(
            run_id="20240116_120000",
            input_source="docs/feature-prd.md",
            start_time="2024-01-16T12:00:00",
            end_time="2024-01-16T12:30:00",
            current_phase="completed",
            prd_title="测试功能",
            total_issues=3,
            success_count=2,
            failed_count=1,
            issue_results=[
                {"number": 1, "title": "Issue 1", "status": "success", "pr_number": 10},
                {"number": 2, "title": "Issue 2", "status": "success", "pr_number": 11},
                {"number": 3, "title": "Issue 3", "status": "failed", "error": "Test error"},
            ],
            pr_results=[
                {"pr_number": 10, "status": "merged"},
                {"pr_number": 11, "status": "merged"},
            ],
        )

    def test_generate_text_report(self):
        """测试生成文本报告"""
        generator = ReportGenerator(self.state)
        report = generator.generate()

        self.assertIn("gh-autopilot", report)
        self.assertIn("测试功能", report)
        self.assertIn("Issue 创建", report)
        self.assertIn("3", report)

    def test_generate_markdown_report(self):
        """测试生成 Markdown 报告"""
        config = ReportConfig(format="markdown")
        generator = ReportGenerator(self.state, config)
        report = generator.generate()

        self.assertIn("# 🚀 gh-autopilot", report)
        self.assertIn("| 指标 |", report)
        self.assertIn("## ✅ 成功合并的 PR", report)

    def test_generate_json_report(self):
        """测试生成 JSON 报告"""
        config = ReportConfig(format="json")
        generator = ReportGenerator(self.state, config)
        report = generator.generate()

        data = json.loads(report)
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["statistics"]["total_issues"], 3)
        self.assertEqual(len(data["pr_results"]), 2)

    def test_truncate_long_text(self):
        """测试截断长文本"""
        generator = ReportGenerator(self.state)
        truncated = generator._truncate("This is a very long text", 10)
        self.assertEqual(truncated, "This is...")

    def test_truncate_short_text(self):
        """测试不截断短文本"""
        generator = ReportGenerator(self.state)
        truncated = generator._truncate("Short", 10)
        self.assertEqual(truncated, "Short")

    def test_calculate_duration(self):
        """测试计算时长"""
        generator = ReportGenerator(self.state)
        duration = generator._calculate_duration()
        self.assertEqual(duration, "30m 0s")

    def test_calculate_duration_no_start(self):
        """测试无开始时间"""
        state = AutopilotState()
        generator = ReportGenerator(state)
        duration = generator._calculate_duration()
        self.assertEqual(duration, "N/A")


class TestGenerateReportFunction(unittest.TestCase):
    """generate_report 便捷函数测试"""

    def test_generate_report_text(self):
        """测试生成文本报告"""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            temp_path = f.name

        try:
            manager = StateManager(temp_path)
            manager.init_state("test")
            manager.complete()

            report = generate_report(manager, format="text")
            self.assertIn("gh-autopilot", report)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)


class TestAutopilotError(unittest.TestCase):
    """AutopilotError 测试"""

    def test_autopilot_error(self):
        """测试自定义异常"""
        error = AutopilotError("Test error message")
        self.assertEqual(str(error), "Test error message")


class TestAutopilot(unittest.TestCase):
    """Autopilot 主类测试"""

    def setUp(self):
        """测试前准备"""
        self.temp_dir = tempfile.mkdtemp()
        self.state_path = os.path.join(self.temp_dir, ".claude", "autopilot-state.json")

    def tearDown(self):
        """测试后清理"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_init_autopilot(self):
        """测试初始化 Autopilot"""
        autopilot = Autopilot(
            input_source="test.md",
            skip_prd=True,
            dry_run=True,
        )
        self.assertEqual(autopilot.input_source, "test.md")
        self.assertTrue(autopilot.skip_prd)
        self.assertTrue(autopilot.dry_run)

    def test_extract_title_markdown(self):
        """测试从 Markdown 提取标题"""
        autopilot = Autopilot("test")
        content = "# Test Feature\n\nDescription here"
        title = autopilot._extract_title(content)
        self.assertEqual(title, "Test Feature")

    def test_extract_title_bold(self):
        """测试从粗体提取标题"""
        autopilot = Autopilot("test")
        content = "**Test Feature**\n\nDescription"
        title = autopilot._extract_title(content)
        self.assertEqual(title, "Test Feature")

    def test_extract_title_fallback(self):
        """测试提取标题回退"""
        autopilot = Autopilot("test")
        content = "Just some plain text content here"
        title = autopilot._extract_title(content)
        self.assertEqual(title, "Just some plain text content here")

    @patch.object(Autopilot, '_invoke_skill_create_issue')
    @patch.object(Autopilot, '_invoke_skill_project_sync')
    def test_dry_run_mode(self, mock_sync, mock_create):
        """测试预览模式"""
        mock_create.return_value = [1, 2, 3]
        mock_sync.return_value = 1

        autopilot = Autopilot(
            input_source="test requirement",
            skip_prd=True,
            dry_run=True,
        )

        with patch.object(autopilot.state_manager, 'init_state'):
            with patch.object(autopilot.state_manager, 'update_phase'):
                with patch.object(autopilot.state_manager, 'set_prd_info'):
                    with patch.object(autopilot.state_manager, 'set_issues'):
                        with patch.object(autopilot.state_manager, 'set_project'):
                            result = autopilot.run()

        self.assertEqual(result, 0)

    def test_log_output(self):
        """测试日志输出"""
        autopilot = Autopilot("test")

        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            autopilot._log("Test message")

        output = f.getvalue()
        self.assertIn("Test message", output)

    def test_log_error_output(self):
        """测试错误日志输出"""
        autopilot = Autopilot("test")

        import io
        from contextlib import redirect_stderr

        f = io.StringIO()
        with redirect_stderr(f):
            autopilot._log("Error message", error=True)

        output = f.getvalue()
        self.assertIn("Error message", output)


class TestAutopilotPhase1(unittest.TestCase):
    """Autopilot 阶段 1 测试"""

    def test_phase1_with_file(self):
        """测试阶段 1 - 文件输入"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Test PRD\n\nContent here")
            temp_path = f.name

        try:
            autopilot = Autopilot(input_source=temp_path, skip_prd=True)
            with patch.object(autopilot.state_manager, 'update_phase'):
                with patch.object(autopilot.state_manager, 'set_prd_info'):
                    content = autopilot._phase_1_requirements()

            self.assertIn("Test PRD", content)
        finally:
            os.remove(temp_path)

    def test_phase1_with_description_skip_prd(self):
        """测试阶段 1 - 描述输入，跳过 PRD"""
        autopilot = Autopilot(
            input_source="Add login feature",
            skip_prd=True,
        )

        with patch.object(autopilot.state_manager, 'update_phase'):
            with patch.object(autopilot.state_manager, 'set_prd_info'):
                content = autopilot._phase_1_requirements()

        self.assertEqual(content, "Add login feature")


class TestAutopilotPhase2(unittest.TestCase):
    """Autopilot 阶段 2 测试"""

    def test_phase2_success(self):
        """测试阶段 2 - 成功创建 Issue"""
        autopilot = Autopilot("test")

        with patch.object(autopilot, '_invoke_skill_create_issue', return_value=[1, 2, 3]):
            with patch.object(autopilot.state_manager, 'update_phase'):
                with patch.object(autopilot.state_manager, 'set_issues'):
                    issues = autopilot._phase_2_create_issues("PRD content")

        self.assertEqual(issues, [1, 2, 3])

    def test_phase2_retry_on_failure(self):
        """测试阶段 2 - 失败重试"""
        autopilot = Autopilot("test")
        autopilot.RETRY_DELAY = 0  # 加速测试

        call_count = [0]

        def mock_create_issue(content):
            call_count[0] += 1
            if call_count[0] < 3:
                raise Exception("API Error")
            return [1, 2]

        with patch.object(autopilot, '_invoke_skill_create_issue', side_effect=mock_create_issue):
            with patch.object(autopilot.state_manager, 'update_phase'):
                with patch.object(autopilot.state_manager, 'set_issues'):
                    issues = autopilot._phase_2_create_issues("PRD")

        self.assertEqual(issues, [1, 2])
        self.assertEqual(call_count[0], 3)


class TestAutopilotPhase3(unittest.TestCase):
    """Autopilot 阶段 3 测试"""

    def test_phase3_skip_sync(self):
        """测试阶段 3 - 跳过同步"""
        autopilot = Autopilot("test", skip_sync=True, project_number=5)

        with patch.object(autopilot.state_manager, 'update_phase'):
            project = autopilot._phase_3_sync_project()

        self.assertEqual(project, 5)

    def test_phase3_with_project_number(self):
        """测试阶段 3 - 指定 Project"""
        autopilot = Autopilot("test", project_number=10)

        with patch.object(autopilot.state_manager, 'update_phase'):
            with patch.object(autopilot.state_manager, 'set_project'):
                project = autopilot._phase_3_sync_project()

        self.assertEqual(project, 10)


class TestAutopilotPhase4(unittest.TestCase):
    """Autopilot 阶段 4 测试"""

    def test_phase4_success(self):
        """测试阶段 4 - 成功实现"""
        autopilot = Autopilot("test")

        mock_results = {
            "results": [
                {"issue_number": 1, "title": "Issue 1", "status": "success", "pr_number": 10},
                {"issue_number": 2, "title": "Issue 2", "status": "failed", "error": "Error"},
            ]
        }

        with patch.object(autopilot, '_invoke_skill_project_implement', return_value=mock_results):
            with patch.object(autopilot.state_manager, 'update_phase'):
                with patch.object(autopilot.state_manager, 'add_issue_result'):
                    autopilot._phase_4_implement(1)

    def test_phase4_failure(self):
        """测试阶段 4 - 实现失败"""
        autopilot = Autopilot("test")

        with patch.object(autopilot, '_invoke_skill_project_implement', side_effect=Exception("Error")):
            with patch.object(autopilot.state_manager, 'update_phase'):
                autopilot._phase_4_implement(1)  # 不应抛出异常


class TestAutopilotPhase5(unittest.TestCase):
    """Autopilot 阶段 5 测试"""

    def test_phase5_success(self):
        """测试阶段 5 - 成功审查"""
        autopilot = Autopilot("test")

        mock_results = {
            "merged": [10, 11],
            "failed": [{"number": 12, "error": "CI failed"}],
        }

        with patch.object(autopilot, '_invoke_skill_project_pr', return_value=mock_results):
            with patch.object(autopilot.state_manager, 'update_phase'):
                with patch.object(autopilot.state_manager, 'add_pr_result'):
                    autopilot._phase_5_review(1)

    def test_phase5_failure(self):
        """测试阶段 5 - 审查失败"""
        autopilot = Autopilot("test")

        with patch.object(autopilot, '_invoke_skill_project_pr', side_effect=Exception("Error")):
            with patch.object(autopilot.state_manager, 'update_phase'):
                autopilot._phase_5_review(1)  # 不应抛出异常


class TestAutopilotPhase6(unittest.TestCase):
    """Autopilot 阶段 6 测试"""

    def test_phase6_report(self):
        """测试阶段 6 - 生成报告"""
        autopilot = Autopilot("test")

        # 初始化状态
        autopilot.state_manager.state.start_time = "2024-01-16T12:00:00"
        autopilot.state_manager.state.prd_title = "Test"

        with patch.object(autopilot.state_manager, 'complete'):
            import io
            from contextlib import redirect_stdout

            f = io.StringIO()
            with redirect_stdout(f):
                autopilot._phase_6_report()

            output = f.getvalue()
            self.assertIn("gh-autopilot", output)


class TestAutopilotInvokeSkills(unittest.TestCase):
    """Autopilot 技能调用测试"""

    def test_invoke_skill_prd(self):
        """测试调用 PRD 技能"""
        autopilot = Autopilot("test")
        result = autopilot._invoke_skill_prd("requirement")
        self.assertIsNone(result)  # 当前实现返回 None

    def test_invoke_skill_create_issue(self):
        """测试调用创建 Issue 技能"""
        autopilot = Autopilot("test")
        result = autopilot._invoke_skill_create_issue("PRD content")
        self.assertEqual(result, [1, 2, 3])  # 当前实现返回模拟数据

    def test_invoke_skill_project_sync(self):
        """测试调用 Project 同步技能"""
        autopilot = Autopilot("test")
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=1)
            result = autopilot._invoke_skill_project_sync()
            self.assertEqual(result, 1)  # 失败时返回默认值

    def test_invoke_skill_project_sync_success(self):
        """测试调用 Project 同步技能成功"""
        autopilot = Autopilot("test")
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(
                returncode=0,
                stdout=json.dumps({"project": {"number": 5}})
            )
            with patch('pathlib.Path.exists', return_value=True):
                result = autopilot._invoke_skill_project_sync()
                self.assertEqual(result, 5)

    def test_invoke_skill_project_implement(self):
        """测试调用 Project 实现技能"""
        autopilot = Autopilot("test")
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=1)
            result = autopilot._invoke_skill_project_implement(1)
            self.assertEqual(result, {"results": []})

    def test_invoke_skill_project_implement_success(self):
        """测试调用 Project 实现技能成功"""
        autopilot = Autopilot("test")
        # batch_executor.py 输出为文本报告，autopilot 会从 stdout 解析执行结果
        stdout = "✅ Issue #1 已完成，PR #10 已合并 (耗时 1m30s)\n"
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(
                returncode=0,
                stdout=stdout
            )
            with patch('pathlib.Path.exists', return_value=True):
                result = autopilot._invoke_skill_project_implement(1)
                self.assertEqual(
                    result,
                    {
                        "results": [
                            {
                                "issue_number": 1,
                                "title": "",
                                "status": "completed",
                                "pr_number": 10,
                                "error": None,
                            }
                        ]
                    },
                )

    def test_invoke_skill_project_pr(self):
        """测试调用 Project PR 技能"""
        autopilot = Autopilot("test")
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=1)
            result = autopilot._invoke_skill_project_pr(1)
            self.assertEqual(result, {"merged": [], "failed": []})

    def test_invoke_skill_project_pr_success(self):
        """测试调用 Project PR 技能成功"""
        autopilot = Autopilot("test")
        # 需要在 state 中提供带 PR 的 issue_results，否则会直接返回空结果
        autopilot.state_manager.state.issue_results = [
            {"number": 1, "title": "Test", "status": "completed", "pr_number": 10},
        ]

        batch_review_output = {
            "results": [
                {"issue": 1, "pr": 10, "status": "merged", "error": None},
            ],
            "summary": {"total": 1, "merged": 1, "failed": 0},
        }
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(
                returncode=0,
                stdout=json.dumps(batch_review_output)
            )
            with patch('pathlib.Path.exists', return_value=True):
                result = autopilot._invoke_skill_project_pr(1)
                self.assertEqual(result, {"merged": [10], "failed": []})


class TestAutopilotRun(unittest.TestCase):
    """Autopilot run 方法完整测试"""

    def test_run_success(self):
        """测试完整执行成功"""
        autopilot = Autopilot("test requirement", skip_prd=True)

        with patch.object(autopilot.state_manager, 'init_state'):
            with patch.object(autopilot, '_phase_1_requirements', return_value="PRD"):
                with patch.object(autopilot, '_phase_2_create_issues', return_value=[1, 2]):
                    with patch.object(autopilot, '_phase_3_sync_project', return_value=1):
                        with patch.object(autopilot, '_phase_4_implement'):
                            with patch.object(autopilot, '_phase_5_review'):
                                with patch.object(autopilot, '_phase_6_report'):
                                    result = autopilot.run()

        self.assertEqual(result, 0)

    def test_run_issue_creation_failed(self):
        """测试 Issue 创建失败"""
        autopilot = Autopilot("test", skip_prd=True)

        with patch.object(autopilot.state_manager, 'init_state'):
            with patch.object(autopilot.state_manager, 'set_error'):
                with patch.object(autopilot.state_manager, 'complete'):
                    with patch.object(autopilot, '_phase_1_requirements', return_value="PRD"):
                        with patch.object(autopilot, '_phase_2_create_issues', return_value=[]):
                            result = autopilot.run()

        self.assertEqual(result, 1)

    def test_run_keyboard_interrupt(self):
        """测试用户中断"""
        autopilot = Autopilot("test", skip_prd=True)

        with patch.object(autopilot.state_manager, 'init_state'):
            with patch.object(autopilot.state_manager, 'set_error'):
                with patch.object(autopilot.state_manager, 'complete'):
                    with patch.object(autopilot, '_phase_1_requirements', side_effect=KeyboardInterrupt()):
                        result = autopilot.run()

        self.assertEqual(result, 130)

    def test_run_unknown_error(self):
        """测试未知错误"""
        autopilot = Autopilot("test", skip_prd=True)

        with patch.object(autopilot.state_manager, 'init_state'):
            with patch.object(autopilot.state_manager, 'set_error'):
                with patch.object(autopilot.state_manager, 'complete'):
                    with patch.object(autopilot, '_phase_1_requirements', side_effect=RuntimeError("Unknown")):
                        result = autopilot.run()

        self.assertEqual(result, 1)


class TestStateManagerEdgeCases(unittest.TestCase):
    """StateManager 边界情况测试"""

    def test_load_invalid_json(self):
        """测试加载无效 JSON"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("invalid json content")
            temp_path = f.name

        try:
            manager = StateManager(temp_path)
            state = manager.load_state()
            self.assertIsNone(state)
        finally:
            os.remove(temp_path)

    def test_calculate_duration_hours(self):
        """测试计算超过一小时的时长"""
        manager = StateManager("/tmp/test.json")
        manager.state.start_time = "2024-01-16T10:00:00"
        manager.state.end_time = "2024-01-16T12:30:45"

        duration = manager._calculate_duration()
        self.assertIn("h", duration)
        self.assertIn("2h", duration)


class TestReportGeneratorEdgeCases(unittest.TestCase):
    """ReportGenerator 边界情况测试"""

    def test_report_many_merged_prs(self):
        """测试大量合并的 PR"""
        state = AutopilotState(
            start_time="2024-01-16T12:00:00",
            end_time="2024-01-16T12:30:00",
            pr_results=[{"pr_number": i, "status": "merged"} for i in range(10)],
        )

        generator = ReportGenerator(state)
        report = generator.generate()
        self.assertIn("还有", report)  # 应该显示"还有 X 个"

    def test_report_many_failed_issues(self):
        """测试大量失败的 Issue"""
        state = AutopilotState(
            start_time="2024-01-16T12:00:00",
            end_time="2024-01-16T12:30:00",
            failed_count=5,
            issue_results=[
                {"number": i, "title": f"Issue {i}", "status": "failed", "error": f"Error {i}"}
                for i in range(5)
            ],
        )

        config = ReportConfig(show_failures=True)
        generator = ReportGenerator(state, config)
        report = generator.generate()
        self.assertIn("还有", report)

    def test_report_no_failures(self):
        """测试无失败项的报告"""
        state = AutopilotState(
            start_time="2024-01-16T12:00:00",
            end_time="2024-01-16T12:30:00",
            failed_count=0,
        )

        config = ReportConfig(show_failures=True)
        generator = ReportGenerator(state, config)
        report = generator.generate()
        # 统计部分始终显示 "失败项: 0 个"，但失败详情部分只在有失败时显示
        self.assertNotIn("❌ 失败项（需人工处理）", report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
