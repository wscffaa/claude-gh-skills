#!/usr/bin/env python3
"""
gh-autopilot 自动测试运行模块。

解析 Test Plan 并自动执行测试命令，收集结果并更新状态。

支持的测试框架:
- pytest (检测 pytest.ini, setup.py, pyproject.toml)
- npm test (检测 package.json)
- make test (检测 Makefile)
"""

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, List, Callable


class TestStatus(str, Enum):
    """测试状态枚举"""
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass
class TestStep:
    """测试步骤数据结构"""
    command: str
    description: str = ""
    expected_output: str = ""
    timeout: int = 300  # 默认 5 分钟超时
    working_dir: Optional[str] = None
    env: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "description": self.description,
            "expected_output": self.expected_output,
            "timeout": self.timeout,
            "working_dir": self.working_dir,
            "env": self.env,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TestStep":
        return cls(
            command=data.get("command", ""),
            description=data.get("description", ""),
            expected_output=data.get("expected_output", ""),
            timeout=data.get("timeout", 300),
            working_dir=data.get("working_dir"),
            env=data.get("env", {}),
        )


@dataclass
class TestStepResult:
    """单个测试步骤的执行结果"""
    step: TestStep
    status: TestStatus
    return_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration: float = 0.0  # 秒
    error_message: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "command": self.step.command,
            "description": self.step.description,
            "status": self.status.value,
            "return_code": self.return_code,
            "stdout": self.stdout[:10000] if self.stdout else "",  # 限制输出大小
            "stderr": self.stderr[:5000] if self.stderr else "",
            "duration": self.duration,
            "error_message": self.error_message,
            "timestamp": self.timestamp,
        }


@dataclass
class TestResults:
    """测试执行结果汇总"""
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    error: int = 0
    total_duration: float = 0.0
    details: List[TestStepResult] = field(default_factory=list)
    start_time: str = ""
    end_time: str = ""

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.skipped + self.error

    @property
    def success_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return self.passed / self.total * 100

    @property
    def all_passed(self) -> bool:
        return self.failed == 0 and self.error == 0

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "error": self.error,
            "total": self.total,
            "total_duration": self.total_duration,
            "success_rate": self.success_rate,
            "all_passed": self.all_passed,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "details": [d.to_dict() for d in self.details],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TestResults":
        results = cls(
            passed=data.get("passed", 0),
            failed=data.get("failed", 0),
            skipped=data.get("skipped", 0),
            error=data.get("error", 0),
            total_duration=data.get("total_duration", 0.0),
            start_time=data.get("start_time", ""),
            end_time=data.get("end_time", ""),
        )
        # details 需要特殊处理，这里简化为不恢复
        return results


class TestRunner:
    """
    测试运行器。

    解析测试计划，执行测试命令，收集并报告结果。
    """

    # 支持的测试框架检测
    FRAMEWORK_DETECTORS = {
        "pytest": ["pytest.ini", "setup.py", "pyproject.toml", "setup.cfg"],
        "npm": ["package.json"],
        "make": ["Makefile", "makefile", "GNUmakefile"],
    }

    # 测试命令模式匹配
    TEST_COMMAND_PATTERNS = [
        r"pytest\s+[\w\-\.\/]+",
        r"python\s+-m\s+pytest\s+[\w\-\.\/]+",
        r"npm\s+(?:run\s+)?test",
        r"yarn\s+test",
        r"make\s+(?:test|lint|check)",
        r"cargo\s+test",
        r"go\s+test",
        r"mvn\s+test",
        r"gradle\s+test",
    ]

    # Test Plan 标记正则
    TEST_PLAN_PATTERNS = [
        r"##\s*Test\s*Plan",
        r"##\s*测试计划",
        r"##\s*Test\s*Focus",
        r"##\s*Test\s*Command",
        r"\*\*Test\s*Command\*\*",
    ]

    def __init__(
        self,
        working_dir: Optional[str] = None,
        on_step_start: Optional[Callable[[TestStep], None]] = None,
        on_step_complete: Optional[Callable[[TestStepResult], None]] = None,
        verbose: bool = False,
    ):
        """
        初始化测试运行器。

        Args:
            working_dir: 工作目录，默认为当前目录
            on_step_start: 步骤开始回调
            on_step_complete: 步骤完成回调
            verbose: 详细输出模式
        """
        self.working_dir = Path(working_dir) if working_dir else Path.cwd()
        self.on_step_start = on_step_start
        self.on_step_complete = on_step_complete
        self.verbose = verbose

    def parse_test_plan(self, source: str) -> List[TestStep]:
        """
        从源文本解析测试计划。

        支持的格式:
        1. Markdown checkbox 格式:
           ## Test Plan
           - [ ] pytest tests/ -v
           - [ ] npm test
           - [ ] make lint

        2. 代码块格式:
           ```bash
           pytest tests/ -v
           ```

        3. dev-plan.md 中的 Test Command 字段:
           - **Test Command**: `pytest tests/ -v`

        Args:
            source: 源文本（PR body, dev-plan.md 内容等）

        Returns:
            解析出的 TestStep 列表
        """
        steps = []
        seen_commands = set()  # 去重

        # 1. 解析 checkbox 格式: - [ ] command 或 - [x] command
        checkbox_pattern = r"-\s*\[[ xX]?\]\s*`?([^`\n]+)`?"
        for match in re.finditer(checkbox_pattern, source):
            command = match.group(1).strip()
            if self._is_test_command(command) and command not in seen_commands:
                steps.append(TestStep(
                    command=command,
                    description=self._extract_description(command),
                ))
                seen_commands.add(command)

        # 2. 解析 Test Command 字段格式
        test_cmd_pattern = r"\*\*Test\s*Command\*\*:\s*`([^`]+)`"
        for match in re.finditer(test_cmd_pattern, source, re.IGNORECASE):
            command = match.group(1).strip()
            if command not in seen_commands:
                steps.append(TestStep(
                    command=command,
                    description="From Test Command field",
                ))
                seen_commands.add(command)

        # 3. 解析 Test Plan 部分下的代码块
        test_plan_section = self._extract_test_plan_section(source)
        if test_plan_section:
            # 解析代码块
            code_block_pattern = r"```(?:bash|sh|shell)?\n([\s\S]*?)```"
            for match in re.finditer(code_block_pattern, test_plan_section):
                for line in match.group(1).strip().split("\n"):
                    command = line.strip()
                    if command and not command.startswith("#") and command not in seen_commands:
                        if self._is_test_command(command):
                            steps.append(TestStep(
                                command=command,
                                description="From code block",
                            ))
                            seen_commands.add(command)

        # 4. 如果没有找到任何测试命令，尝试自动检测
        if not steps:
            steps = self._auto_detect_test_commands()

        return steps

    def _extract_test_plan_section(self, source: str) -> str:
        """提取 Test Plan 部分内容"""
        for pattern in self.TEST_PLAN_PATTERNS:
            match = re.search(pattern, source, re.IGNORECASE)
            if match:
                start = match.end()
                # 找到下一个 ## 标题或文档结尾
                next_section = re.search(r"\n##\s", source[start:])
                if next_section:
                    return source[start:start + next_section.start()]
                return source[start:]
        return ""

    def _is_test_command(self, command: str) -> bool:
        """判断是否为测试命令"""
        command_lower = command.lower()
        test_keywords = [
            "pytest", "test", "spec", "check", "lint",
            "npm test", "yarn test", "make test", "cargo test",
            "go test", "mvn test", "gradle test",
        ]
        return any(keyword in command_lower for keyword in test_keywords)

    def _extract_description(self, command: str) -> str:
        """从命令提取描述"""
        if "pytest" in command.lower():
            return "Run pytest tests"
        elif "npm" in command.lower():
            return "Run npm tests"
        elif "make" in command.lower():
            if "lint" in command.lower():
                return "Run linting"
            return "Run make tests"
        elif "cargo" in command.lower():
            return "Run Rust tests"
        elif "go test" in command.lower():
            return "Run Go tests"
        return f"Run: {command[:50]}"

    def _auto_detect_test_commands(self) -> List[TestStep]:
        """自动检测可用的测试命令"""
        steps = []

        # 检测 pytest
        for marker in self.FRAMEWORK_DETECTORS["pytest"]:
            if (self.working_dir / marker).exists():
                # 检查是否有 tests 目录
                tests_dir = self.working_dir / "tests"
                if tests_dir.exists():
                    steps.append(TestStep(
                        command="pytest tests/ -v",
                        description="Auto-detected pytest",
                    ))
                else:
                    steps.append(TestStep(
                        command="pytest -v",
                        description="Auto-detected pytest",
                    ))
                break

        # 检测 npm
        package_json = self.working_dir / "package.json"
        if package_json.exists():
            try:
                with open(package_json, "r", encoding="utf-8") as f:
                    pkg = json.load(f)
                if "scripts" in pkg and "test" in pkg["scripts"]:
                    steps.append(TestStep(
                        command="npm test",
                        description="Auto-detected npm test",
                    ))
            except (json.JSONDecodeError, IOError):
                pass

        # 检测 Makefile
        for marker in self.FRAMEWORK_DETECTORS["make"]:
            makefile = self.working_dir / marker
            if makefile.exists():
                try:
                    content = makefile.read_text(encoding="utf-8")
                    if re.search(r"^test\s*:", content, re.MULTILINE):
                        steps.append(TestStep(
                            command="make test",
                            description="Auto-detected make test",
                        ))
                    if re.search(r"^lint\s*:", content, re.MULTILINE):
                        steps.append(TestStep(
                            command="make lint",
                            description="Auto-detected make lint",
                        ))
                except IOError:
                    pass
                break

        return steps

    def execute_tests(
        self,
        steps: List[TestStep],
        stop_on_failure: bool = False,
    ) -> TestResults:
        """
        执行测试步骤。

        Args:
            steps: 测试步骤列表
            stop_on_failure: 失败时是否停止

        Returns:
            TestResults 汇总结果
        """
        results = TestResults(start_time=datetime.now().isoformat())

        for step in steps:
            # 回调: 步骤开始
            if self.on_step_start:
                self.on_step_start(step)

            # 执行测试
            step_result = self._execute_single_step(step)
            results.details.append(step_result)

            # 更新统计
            if step_result.status == TestStatus.PASSED:
                results.passed += 1
            elif step_result.status == TestStatus.FAILED:
                results.failed += 1
            elif step_result.status == TestStatus.SKIPPED:
                results.skipped += 1
            else:
                results.error += 1

            results.total_duration += step_result.duration

            # 回调: 步骤完成
            if self.on_step_complete:
                self.on_step_complete(step_result)

            # 失败时停止
            if stop_on_failure and step_result.status in (TestStatus.FAILED, TestStatus.ERROR):
                # 标记剩余步骤为 skipped
                remaining_idx = steps.index(step) + 1
                for remaining_step in steps[remaining_idx:]:
                    skipped_result = TestStepResult(
                        step=remaining_step,
                        status=TestStatus.SKIPPED,
                        error_message="Skipped due to previous failure",
                        timestamp=datetime.now().isoformat(),
                    )
                    results.details.append(skipped_result)
                    results.skipped += 1
                break

        results.end_time = datetime.now().isoformat()
        return results

    def _execute_single_step(self, step: TestStep) -> TestStepResult:
        """执行单个测试步骤"""
        timestamp = datetime.now().isoformat()
        start_time = time.time()

        # 确定工作目录
        cwd = Path(step.working_dir) if step.working_dir else self.working_dir

        # 准备环境变量
        env = os.environ.copy()
        env.update(step.env)

        try:
            # 检查命令是否可执行
            cmd_parts = step.command.split()
            if not cmd_parts:
                return TestStepResult(
                    step=step,
                    status=TestStatus.ERROR,
                    error_message="Empty command",
                    timestamp=timestamp,
                )

            # 检查可执行文件是否存在
            executable = cmd_parts[0]
            if not shutil.which(executable):
                return TestStepResult(
                    step=step,
                    status=TestStatus.SKIPPED,
                    error_message=f"Executable not found: {executable}",
                    timestamp=timestamp,
                )

            # 执行命令
            result = subprocess.run(
                step.command,
                shell=True,
                cwd=str(cwd),
                env=env,
                capture_output=True,
                text=True,
                timeout=step.timeout,
            )

            duration = time.time() - start_time

            # 判断结果
            if result.returncode == 0:
                status = TestStatus.PASSED
            else:
                status = TestStatus.FAILED

            return TestStepResult(
                step=step,
                status=status,
                return_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration=duration,
                timestamp=timestamp,
            )

        except subprocess.TimeoutExpired as e:
            duration = time.time() - start_time
            return TestStepResult(
                step=step,
                status=TestStatus.ERROR,
                error_message=f"Timeout after {step.timeout}s",
                duration=duration,
                stdout=e.stdout.decode() if e.stdout else "",
                stderr=e.stderr.decode() if e.stderr else "",
                timestamp=timestamp,
            )

        except Exception as e:
            duration = time.time() - start_time
            return TestStepResult(
                step=step,
                status=TestStatus.ERROR,
                error_message=str(e),
                duration=duration,
                timestamp=timestamp,
            )

    def report_results(
        self,
        results: TestResults,
        state_manager=None,
        pr_number: Optional[int] = None,
    ) -> str:
        """
        报告测试结果。

        Args:
            results: 测试结果
            state_manager: 状态管理器（可选，用于更新 state.test_results）
            pr_number: PR 编号（可选，用于发布评论）

        Returns:
            格式化的报告文本
        """
        # 更新状态
        if state_manager:
            self._update_state(state_manager, results)

        # 生成报告
        report = self._generate_report(results)

        # 发布到 PR（可选）
        if pr_number:
            self._post_to_pr(pr_number, report)

        return report

    def _update_state(self, state_manager, results: TestResults) -> None:
        """更新状态管理器中的测试结果"""
        # 检查 state 是否有 test_results 字段
        if hasattr(state_manager.state, "test_results"):
            state_manager.state.test_results.append(results.to_dict())
            state_manager._save()

    def _generate_report(self, results: TestResults) -> str:
        """生成测试报告"""
        lines = [
            "## Test Results",
            "",
            f"**Status**: {'✅ All Passed' if results.all_passed else '❌ Some Failed'}",
            f"**Total**: {results.total} tests",
            f"**Passed**: {results.passed}",
            f"**Failed**: {results.failed}",
            f"**Skipped**: {results.skipped}",
            f"**Errors**: {results.error}",
            f"**Duration**: {results.total_duration:.2f}s",
            f"**Success Rate**: {results.success_rate:.1f}%",
            "",
        ]

        # 详细结果
        if results.details:
            lines.append("### Details")
            lines.append("")
            for detail in results.details:
                icon = {
                    TestStatus.PASSED: "✅",
                    TestStatus.FAILED: "❌",
                    TestStatus.SKIPPED: "⏭️",
                    TestStatus.ERROR: "⚠️",
                }.get(detail.status, "❓")

                lines.append(f"- {icon} `{detail.step.command}` ({detail.duration:.2f}s)")
                if detail.error_message:
                    lines.append(f"  - Error: {detail.error_message}")
                if detail.status == TestStatus.FAILED and detail.stderr:
                    # 截取错误信息
                    stderr_preview = detail.stderr[:500].replace("\n", "\n    ")
                    lines.append(f"  ```\n    {stderr_preview}\n  ```")

        return "\n".join(lines)

    def _post_to_pr(self, pr_number: int, report: str) -> bool:
        """发布报告到 PR 评论"""
        try:
            # 使用 gh CLI 发布评论
            result = subprocess.run(
                ["gh", "pr", "comment", str(pr_number), "--body", report],
                capture_output=True,
                text=True,
                timeout=60,
            )
            return result.returncode == 0
        except Exception:
            return False


def parse_dev_plan_tests(dev_plan_path: str) -> List[TestStep]:
    """
    从 dev-plan.md 解析测试命令。

    Args:
        dev_plan_path: dev-plan.md 文件路径

    Returns:
        TestStep 列表
    """
    path = Path(dev_plan_path)
    if not path.exists():
        return []

    content = path.read_text(encoding="utf-8")
    runner = TestRunner(working_dir=str(path.parent))
    return runner.parse_test_plan(content)


if __name__ == "__main__":  # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser(description="Test Runner for gh-autopilot")
    parser.add_argument(
        "source",
        nargs="?",
        help="Source file (dev-plan.md, PR body file) or test commands",
    )
    parser.add_argument(
        "--command", "-c",
        action="append",
        help="Direct test command to run (can be repeated)",
    )
    parser.add_argument(
        "--stop-on-failure",
        action="store_true",
        help="Stop execution on first failure",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--pr",
        type=int,
        help="Post results to PR comment",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args()

    # 创建运行器
    def on_step_start(step: TestStep):
        print(f"🧪 Running: {step.command}")

    def on_step_complete(result: TestStepResult):
        icon = "✅" if result.status == TestStatus.PASSED else "❌"
        print(f"   {icon} {result.status.value} ({result.duration:.2f}s)")

    runner = TestRunner(
        on_step_start=on_step_start if args.verbose else None,
        on_step_complete=on_step_complete if args.verbose else None,
        verbose=args.verbose,
    )

    # 解析测试步骤
    steps = []

    if args.command:
        steps = [TestStep(command=cmd) for cmd in args.command]
    elif args.source:
        source_path = Path(args.source)
        if source_path.exists():
            content = source_path.read_text(encoding="utf-8")
            steps = runner.parse_test_plan(content)
        else:
            # 作为直接命令处理
            steps = [TestStep(command=args.source)]
    else:
        # 自动检测
        steps = runner._auto_detect_test_commands()

    if not steps:
        print("No test commands found.")
        exit(0)

    # 执行测试
    results = runner.execute_tests(steps, stop_on_failure=args.stop_on_failure)

    # 输出结果
    if args.json:
        print(json.dumps(results.to_dict(), indent=2, ensure_ascii=False))
    else:
        report = runner.report_results(results, pr_number=args.pr)
        print(report)

    # 退出码
    exit(0 if results.all_passed else 1)
