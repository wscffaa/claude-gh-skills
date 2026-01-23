#!/usr/bin/env python3
"""
gh-autopilot 主编排脚本。

从 PRD 到代码合并的全自动化流水线。

用法:
    # 基于 PRD 文件启动
    python3 autopilot.py docs/feature-prd.md

    # 基于需求描述启动
    python3 autopilot.py "添加用户登录功能"

    # 预览模式
    python3 autopilot.py docs/feature-prd.md --dry-run

    # 跳过 PRD 生成
    python3 autopilot.py docs/feature-prd.md --skip-prd

    # 指定 Project
    python3 autopilot.py docs/feature-prd.md --project 1
"""

import argparse
import json
import logging
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

from state import StateManager, Phase, IssueResult, get_state_manager, ResumeInfo, Checkpoint
from report import ReportGenerator, ReportConfig
from safe_command import (
    SafeCommandBuilder,
    run_command_with_stdin,
    build_python_script_command,
    escape_for_logging,
)
from dependency_validator import (
    DependencyValidator,
    DependencyValidatorError,
    get_validator,
)
from retry import (
    RetryPolicy,
    RetryExecutor,
    RetryResult,
    categorize_error,
    is_retryable,
    ErrorCategory,
    TransientError,
    RateLimitError,
    ClientError,
    PermanentError,
    DEFAULT_RETRY_POLICY,
    API_RETRY_POLICY,
)
from test_runner import (
    TestRunner,
    TestStep,
    TestResults,
    TestStatus,
    parse_dev_plan_tests,
)

# 配置日志
logger = logging.getLogger("gh-autopilot")


class AutopilotError(Exception):
    """Autopilot 执行错误"""
    pass


class Autopilot:
    """Autopilot 主执行器"""

    # 默认重试策略配置
    DEFAULT_RETRY_POLICY = RetryPolicy(
        max_retries=3,
        base_delay=2.0,
        max_delay=60.0,
        exponential_base=2.0,
        jitter=True,
        jitter_factor=0.3,
    )

    def __init__(
        self,
        input_source: str,
        skip_prd: bool = False,
        skip_sync: bool = False,
        dry_run: bool = False,
        project_number: Optional[int] = None,
        priority_filter: Optional[str] = None,
        verbose: bool = False,
        retry_policy: Optional[RetryPolicy] = None,
        resume: bool = False,
        resume_run_id: Optional[str] = None,
    ):
        self.input_source = input_source
        self.skip_prd = skip_prd
        self.skip_sync = skip_sync
        self.dry_run = dry_run
        self.project_number = project_number
        self.priority_filter = priority_filter
        self.verbose = verbose
        self.retry_policy = retry_policy or self.DEFAULT_RETRY_POLICY
        self.resume = resume
        self.resume_run_id = resume_run_id
        self.resume_info: Optional[ResumeInfo] = None

        self.state_manager = get_state_manager()

        # 创建重试执行器，带日志回调
        self.retry_executor = RetryExecutor(
            policy=self.retry_policy,
            on_retry=self._on_retry_callback,
            on_failure=self._on_failure_callback,
        )

    def _on_retry_callback(self, attempt: int, exception: Exception, delay: float) -> None:
        """重试回调 - 记录重试信息"""
        category = categorize_error(exception)
        self._log(f"   ⚠️ 尝试 {attempt + 1}/{self.retry_policy.max_retries + 1} 失败 ({category.value}): {exception}")
        self._log(f"   ⏳ {delay:.1f}s 后重试...")
        logger.info(f"Retry attempt {attempt + 1}, delay {delay:.2f}s, error: {exception}")

    def _on_failure_callback(self, exception: Exception, total_attempts: int) -> None:
        """最终失败回调"""
        category = categorize_error(exception)
        self._log(f"   ❌ 重试耗尽 ({total_attempts} 次尝试), 错误类型: {category.value}")
        logger.error(f"All retries exhausted after {total_attempts} attempts: {exception}")

    def run(self) -> int:
        """执行完整流程"""
        try:
            # 处理恢复逻辑
            if self.resume:
                self.resume_info = self.state_manager.resume_from_checkpoint(self.resume_run_id)
                if not self.resume_info:
                    self._log("❌ 无法恢复：没有找到可恢复的运行状态", error=True)
                    return 1
                self._log(f"🔄 从检查点恢复运行")
                self._log(f"   原始 run_id: {self.resume_info.original_run_id}")
                self._log(f"   恢复阶段: {self.resume_info.resume_phase.value}")
                self._log(f"   最后成功步骤: {self.resume_info.last_successful_step}")
            else:
                # 初始化新状态
                self.state_manager.init_state(self.input_source)
                self._log("🚀 gh-autopilot 启动")
                self._log(f"   输入: {self.input_source}")

            if self.dry_run:
                self._log("   模式: 预览 (dry-run)")

            # 阶段 1: 需求确认
            prd_content = self._phase_1_requirements()

            # 阶段 2: 创建 Issue
            issues = self._phase_2_create_issues(prd_content)
            if not issues:
                raise AutopilotError("Issue 创建失败")

            # 阶段 3: 同步到 Project
            project_number = self._phase_3_sync_project()

            if self.dry_run:
                self._log("\n✅ 预览完成 (dry-run 模式)")
                self._log(f"   将创建 {len(issues)} 个 Issue")
                self._log(f"   将同步到 Project #{project_number}")
                return 0

            # 阶段 4: 并发实现
            self._phase_4_implement(project_number)

            # 阶段 4.5: 自动测试运行
            self._phase_4_5_test_run()

            # 阶段 5: 批量审查
            self._phase_5_review(project_number)

            # 阶段 6: 完成报告
            self._phase_6_report()

            return 0

        except AutopilotError as e:
            self._log(f"\n❌ 执行失败: {e}", error=True)
            self.state_manager.record_error(
                Phase(self.state_manager.state.current_phase),
                "run",
                e,
                recoverable=True,
            )
            self.state_manager.complete(success=False)
            return 1

        except KeyboardInterrupt:
            self._log("\n⚠️ 用户中断", error=True)
            self.state_manager.record_error(
                Phase(self.state_manager.state.current_phase),
                "run",
                Exception("用户中断"),
                recoverable=True,
            )
            self.state_manager.complete(success=False)
            return 130

        except Exception as e:
            self._log(f"\n❌ 未知错误: {e}", error=True)
            self.state_manager.record_error(
                Phase(self.state_manager.state.current_phase),
                "run",
                e,
                recoverable=True,
            )
            self.state_manager.complete(success=False)
            return 1

    def _should_skip_phase(self, phase: Phase) -> bool:
        """检查是否应跳过某个阶段（恢复模式下已完成的阶段）"""
        if not self.resume_info:
            return False
        return self.resume_info.should_skip_phase(phase)

    def _is_step_completed(self, phase: Phase, step: str) -> bool:
        """检查步骤是否已完成（幂等性检查）"""
        return self.state_manager.is_step_completed(phase, step)

    def _phase_1_requirements(self) -> str:
        """阶段 1: 需求确认"""
        # 检查是否应跳过此阶段（恢复模式）
        if self._should_skip_phase(Phase.PRD):
            self._log("\n🔍 阶段 1/6: 需求确认... (跳过 - 已完成)")
            # 从上下文恢复 PRD 内容
            prd_path = self.resume_info.get_context_value("prd_path", "")
            if prd_path and Path(prd_path).exists():
                return Path(prd_path).read_text(encoding="utf-8")
            return self.input_source

        self._log("\n🔍 阶段 1/6: 需求确认...")
        self.state_manager.update_phase(Phase.PRD)

        # 检查输入是否为文件
        input_path = Path(self.input_source)
        if input_path.exists() and input_path.suffix in (".md", ".txt"):
            # 幂等性检查
            if self._is_step_completed(Phase.PRD, "prd_read"):
                self._log(f"   读取 PRD 文件: {self.input_source} (已完成)")
            else:
                self._log(f"   读取 PRD 文件: {self.input_source}")
            prd_content = input_path.read_text(encoding="utf-8")
            self.state_manager.set_prd_info(str(input_path), self._extract_title(prd_content))
            # 保存检查点
            self.state_manager.checkpoint(
                Phase.PRD,
                "prd_read",
                context={"prd_path": str(input_path)},
            )
            return prd_content

        # 输入为需求描述
        if self.skip_prd:
            self._log("   跳过 PRD 生成，使用原始需求描述")
            self.state_manager.set_prd_info("", self.input_source[:50])
            self.state_manager.checkpoint(Phase.PRD, "prd_skip")
            return self.input_source

        # 调用 /product-requirements 生成 PRD
        if not self._is_step_completed(Phase.PRD, "prd_generate"):
            self._log("   调用 /product-requirements 生成 PRD...")
            prd_path = self._invoke_skill_prd(self.input_source)
            if prd_path and Path(prd_path).exists():
                prd_content = Path(prd_path).read_text(encoding="utf-8")
                self.state_manager.set_prd_info(prd_path, self._extract_title(prd_content))
                self.state_manager.checkpoint(
                    Phase.PRD,
                    "prd_generate",
                    context={"prd_path": prd_path},
                )
                return prd_content

        # PRD 生成失败，使用原始描述
        self._log("   ⚠️ PRD 生成失败，使用原始需求描述")
        self.state_manager.set_prd_info("", self.input_source[:50])
        self.state_manager.checkpoint(Phase.PRD, "prd_fallback")
        return self.input_source

    def _phase_2_create_issues(self, prd_content: str) -> list[int]:
        """阶段 2: 创建 Issue"""
        # 检查是否应跳过此阶段（恢复模式）
        if self._should_skip_phase(Phase.CREATE_ISSUE):
            self._log("\n📝 阶段 2/6: 创建 Issue... (跳过 - 已完成)")
            return self.resume_info.get_context_value("issues_created", [])

        self._log("\n📝 阶段 2/6: 创建 Issue...")
        self.state_manager.update_phase(Phase.CREATE_ISSUE)

        # 幂等性检查
        if self._is_step_completed(Phase.CREATE_ISSUE, "issues_created"):
            self._log("   ✅ Issue 已创建 (跳过)")
            return self.state_manager.state.issues_created

        # 使用 RetryExecutor 执行带重试的 Issue 创建
        result = self.retry_executor.execute(
            self._invoke_skill_create_issue,
            prd_content,
            fallback=self._fallback_create_issue,
            fallback_args=(prd_content,),
        )

        if result.success and result.result:
            issues = result.result
            self._log(f"   ✅ 创建了 {len(issues)} 个 Issue (尝试 {result.attempts} 次, 延迟 {result.total_delay:.1f}s)")
            self.state_manager.set_issues(issues)
            # 保存检查点
            self.state_manager.checkpoint(
                Phase.CREATE_ISSUE,
                "issues_created",
                context={"issues": issues},
            )
            return issues

        # 记录失败信息
        if result.exception:
            self._log(f"   ❌ Issue 创建失败: {result.exception}")
            self.state_manager.record_error(
                Phase.CREATE_ISSUE,
                "issues_created",
                result.exception,
                recoverable=True,
            )

        return []

    def _fallback_create_issue(self, prd_content: str) -> list[int]:
        """
        降级方法: 使用 gh CLI 直接创建 Issue。

        当主方法失败时，尝试使用更简单的方式创建 Issue。
        """
        self._log("   🔄 尝试降级方法: 使用 gh CLI 直接创建...")
        try:
            # 提取标题作为 Issue 标题
            title = self._extract_title(prd_content)[:100]
            body = prd_content[:65000]  # GitHub Issue body 限制

            result = subprocess.run(
                ["gh", "issue", "create", "--title", title, "--body", body],
                capture_output=True,
                text=True,
                timeout=60,
            )

            if result.returncode == 0:
                # 解析返回的 Issue URL，提取编号
                # 格式: https://github.com/owner/repo/issues/123
                url = result.stdout.strip()
                if "/issues/" in url:
                    issue_number = int(url.split("/issues/")[-1])
                    self._log(f"   ✅ 降级方法成功，创建了 Issue #{issue_number}")
                    return [issue_number]

            raise TransientError(f"gh CLI 创建失败: {result.stderr}")
        except subprocess.TimeoutExpired:
            raise TransientError("gh CLI 超时")
        except Exception as e:
            if isinstance(e, TransientError):
                raise
            raise TransientError(f"降级方法失败: {e}")

    def _phase_3_sync_project(self) -> int:
        """阶段 3: 同步到 Project"""
        self._log("\n📋 阶段 3/6: 同步到 Project...")
        self.state_manager.update_phase(Phase.PROJECT_SYNC)

        if self.skip_sync:
            self._log("   跳过 Project 同步")
            return self.project_number or 1

        if self.project_number:
            self._log(f"   使用指定的 Project #{self.project_number}")
            self.state_manager.set_project(self.project_number)
            return self.project_number

        # 使用 RetryExecutor 执行带重试的 Project 同步
        result = self.retry_executor.execute(
            self._invoke_skill_project_sync,
            fallback=self._fallback_project_sync,
        )

        if result.success and result.result:
            project_number = result.result
            self._log(f"   ✅ 同步到 Project #{project_number} (尝试 {result.attempts} 次)")
            self.state_manager.set_project(project_number)
            return project_number

        # 同步失败，非关键路径，使用默认值继续执行
        self._log("   ⚠️ Project 同步失败，使用默认 Project #1 继续执行...")
        return self.project_number or 1

    def _fallback_project_sync(self) -> Optional[int]:
        """
        降级方法: 返回默认 Project 编号。

        当主方法失败时，使用默认值继续流程。
        """
        self._log("   🔄 降级: 使用默认 Project #1")
        return 1

    def _phase_4_implement(self, project_number: int) -> None:
        """阶段 4: 并发实现"""
        self._log("\n🔨 阶段 4/6: 并发实现...")
        self.state_manager.update_phase(Phase.IMPLEMENT)

        try:
            results = self._invoke_skill_project_implement(project_number)
            self._log(f"   ✅ 实现完成")

            # 记录结果
            for result in results.get("results", []):
                issue_result = IssueResult(
                    number=result.get("issue_number", 0),
                    title=result.get("title", ""),
                    status=result.get("status", "unknown"),
                    pr_number=result.get("pr_number"),
                    error=result.get("error"),
                )
                self.state_manager.add_issue_result(issue_result)

        except Exception as e:
            self._log(f"   ⚠️ 部分 Issue 实现失败: {e}")

    def _phase_4_5_test_run(self) -> None:
        """阶段 4.5: 自动测试运行"""
        # 检查是否应跳过此阶段（恢复模式）
        if self._should_skip_phase(Phase.TEST_RUN):
            self._log("\n🧪 阶段 4.5/6: 自动测试运行... (跳过 - 已完成)")
            return

        self._log("\n🧪 阶段 4.5/6: 自动测试运行...")
        self.state_manager.update_phase(Phase.TEST_RUN)

        # 幂等性检查
        if self._is_step_completed(Phase.TEST_RUN, "tests_executed"):
            self._log("   ✅ 测试已执行 (跳过)")
            return

        try:
            # 创建测试运行器
            runner = TestRunner(
                on_step_start=lambda step: self._log(f"   🔄 运行: {step.command}") if self.verbose else None,
                on_step_complete=lambda result: self._log(
                    f"   {'✅' if result.status == TestStatus.PASSED else '❌'} {result.status.value} ({result.duration:.2f}s)"
                ) if self.verbose else None,
                verbose=self.verbose,
            )

            # 尝试从多个来源解析测试计划
            steps = []

            # 1. 从 PRD 路径解析 dev-plan.md
            prd_path = self.state_manager.state.prd_path
            if prd_path:
                prd_dir = Path(prd_path).parent
                dev_plan_path = prd_dir / "dev-plan.md"
                if dev_plan_path.exists():
                    self._log(f"   📄 从 {dev_plan_path} 解析测试计划")
                    steps = parse_dev_plan_tests(str(dev_plan_path))

            # 2. 如果没有找到测试步骤，尝试自动检测
            if not steps:
                self._log("   🔍 自动检测测试命令...")
                steps = runner._auto_detect_test_commands()

            if not steps:
                self._log("   ⚠️ 未找到测试命令，跳过测试阶段")
                self.state_manager.checkpoint(
                    Phase.TEST_RUN,
                    "tests_executed",
                    context={"skipped": True, "reason": "No test commands found"},
                )
                return

            self._log(f"   📋 找到 {len(steps)} 个测试命令")

            # 执行测试
            results = runner.execute_tests(steps, stop_on_failure=False)

            # 更新状态
            self.state_manager.state.test_results.append(results.to_dict())
            self.state_manager._save()

            # 报告结果
            if results.all_passed:
                self._log(f"   ✅ 所有测试通过 ({results.passed}/{results.total})")
            else:
                self._log(f"   ⚠️ 部分测试失败 (通过: {results.passed}, 失败: {results.failed}, 错误: {results.error})")

                # 记录失败的测试到错误历史
                for detail in results.details:
                    if detail.status in (TestStatus.FAILED, TestStatus.ERROR):
                        self.state_manager.record_error(
                            Phase.TEST_RUN,
                            f"test:{detail.step.command[:50]}",
                            Exception(detail.error_message or f"Test failed with exit code {detail.return_code}"),
                            recoverable=True,
                        )

            # 保存检查点
            self.state_manager.checkpoint(
                Phase.TEST_RUN,
                "tests_executed",
                context={
                    "total": results.total,
                    "passed": results.passed,
                    "failed": results.failed,
                    "all_passed": results.all_passed,
                },
            )

        except Exception as e:
            self._log(f"   ❌ 测试运行失败: {e}")
            self.state_manager.record_error(
                Phase.TEST_RUN,
                "tests_executed",
                e,
                recoverable=True,
            )

    def _phase_5_review(self, project_number: int) -> None:
        """阶段 5: 批量审查"""
        self._log("\n🔍 阶段 5/6: 批量 PR 审查...")
        self.state_manager.update_phase(Phase.PR_REVIEW)

        try:
            results = self._invoke_skill_project_pr(project_number)
            self._log(f"   ✅ 审查完成")

            # 记录结果
            for pr in results.get("merged", []):
                self.state_manager.add_pr_result(pr, "merged")
            for pr in results.get("failed", []):
                self.state_manager.add_pr_result(pr["number"], "failed", pr.get("error"))

        except Exception as e:
            self._log(f"   ⚠️ 部分 PR 审查失败: {e}")

    def _phase_6_report(self) -> None:
        """阶段 6: 完成报告"""
        self._log("\n📊 阶段 6/6: 生成报告...")
        self.state_manager.complete(success=True)

        # 生成报告
        config = ReportConfig(show_details=True, show_failures=True)
        generator = ReportGenerator(self.state_manager.state, config)
        report = generator.generate()

        print("\n" + report)

    # === 技能调用方法 ===

    def _invoke_skill_prd(self, requirement: str) -> Optional[str]:
        """调用 /product-requirements"""
        # 实际实现中通过 Claude CLI 调用
        # 这里返回模拟结果
        self._log("   (调用 /product-requirements)")
        return None  # 让上层使用原始需求

    def _invoke_skill_create_issue(self, prd_content: str) -> list[int]:
        """调用 /gh-create-issue"""
        self._log("   (调用 /gh-create-issue)")

        # 通过 Claude CLI 调用技能
        # 实际命令: claude -p "基于以下 PRD 创建 Issue: {prd_content}" --skill gh-create-issue
        # 这里使用 gh CLI 直接创建作为后备

        # 返回模拟的 Issue 编号（实际实现中解析命令输出）
        return [1, 2, 3]  # 模拟数据

    def _invoke_skill_project_sync(self) -> Optional[int]:
        """调用 /gh-project-sync"""
        self._log("   (调用 /gh-project-sync)")

        # 实际调用 gh-project-sync 脚本
        try:
            script_path = Path(__file__).parent.parent.parent / "gh-project-sync" / "scripts" / "sync_project.py"
            if script_path.exists():
                # sync_project.py 需要 --project 参数（必选）
                # 还需要 --issues 或 --all 或 --epic 参数之一
                project_num = self.project_number or 1
                issues_created = self.state_manager.state.issues_created

                args = ["python3", str(script_path), "--project", str(project_num)]

                if issues_created:
                    # 将创建的 Issue 列表转换为逗号分隔的字符串
                    issues_str = ",".join(str(n) for n in issues_created)
                    args.extend(["--issues", issues_str])
                else:
                    # 如果没有指定 issues，使用 --all 同步所有 open issues
                    args.append("--all")

                args.append("--json")

                result = subprocess.run(
                    args,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode == 0:
                    data = json.loads(result.stdout)
                    # 返回 project.number 或直接返回传入的 project_num
                    return data.get("project", {}).get("number", project_num)
        except Exception as e:
            self._log(f"   调用失败: {e}")

        return self.project_number or 1  # 默认返回指定的 project 或 1

    def _invoke_skill_project_implement(self, project_number: int) -> dict:
        """调用 /gh-project-implement"""
        self._log(f"   (调用 /gh-project-implement {project_number})")

        # batch_executor.py 需要从 stdin 或 --input 读取 JSON
        # JSON 格式来自 priority_batcher.py 输出:
        # {"batches": [{"priority": "p0", "issues": [{"number": 42, "title": "xxx"}]}]}
        try:
            script_path = Path(__file__).parent.parent.parent / "gh-project-implement" / "scripts" / "batch_executor.py"
            if script_path.exists():
                # 构建 batch_executor 需要的输入 JSON
                # 将 state 中的 issues 转换为 batches 格式
                issues_created = self.state_manager.state.issues_created or []
                batches_input = {
                    "batches": [
                        {
                            "priority": "p1",  # 默认优先级
                            "issues": [{"number": n, "title": "", "dependencies": []} for n in issues_created]
                        }
                    ]
                }

                # 如果有优先级过滤，应用过滤
                if self.priority_filter:
                    priorities = [p.strip().lower() for p in self.priority_filter.split(",")]
                    batches_input["batches"][0]["priority"] = priorities[0] if priorities else "p1"

                input_json = json.dumps(batches_input, ensure_ascii=False)

                # 通过 stdin 传递 JSON 数据
                result = subprocess.run(
                    ["python3", str(script_path)],
                    input=input_json,
                    capture_output=True,
                    text=True,
                    timeout=7200,  # 2 hours
                )
                if result.returncode == 0:
                    # batch_executor 的输出是执行报告，不是 JSON
                    # 解析 stdout 中的执行结果
                    return self._parse_batch_executor_output(result.stdout)
                else:
                    self._log(f"   batch_executor 返回码: {result.returncode}")
                    if result.stderr:
                        self._log(f"   stderr: {result.stderr[:500]}")
        except json.JSONDecodeError as e:
            self._log(f"   JSON 解析失败: {e}")
        except Exception as e:
            self._log(f"   调用失败: {e}")

        return {"results": []}

    def _parse_batch_executor_output(self, stdout: str) -> dict:
        """解析 batch_executor.py 的输出"""
        results = []
        # batch_executor 输出格式包含:
        # ✅ Issue #42 已完成，PR #123 已合并 (耗时 2m30s)
        # ❌ Issue #42 失败 (尝试 2/4): xxx
        import re

        # 匹配成功的 issue
        success_pattern = r"✅ Issue #(\d+) 已完成(?:，PR #(\d+) 已合并)?"
        for match in re.finditer(success_pattern, stdout):
            issue_num = int(match.group(1))
            pr_num = int(match.group(2)) if match.group(2) else None
            results.append({
                "issue_number": issue_num,
                "title": "",
                "status": "completed",
                "pr_number": pr_num,
                "error": None,
            })

        # 匹配失败的 issue
        fail_pattern = r"❌ Issue #(\d+) 失败.*?: (.+)"
        for match in re.finditer(fail_pattern, stdout):
            issue_num = int(match.group(1))
            error_msg = match.group(2).strip()
            results.append({
                "issue_number": issue_num,
                "title": "",
                "status": "failed",
                "pr_number": None,
                "error": error_msg,
            })

        return {"results": results}

    def _invoke_skill_project_pr(self, project_number: int) -> dict:
        """调用 /gh-project-pr (batch_review.py)"""
        self._log(f"   (调用 /gh-project-pr {project_number} --auto-merge --review-backend codex)")

        # 使用 batch_review.py 替代 main.py（main.py 的 Phase 4-6 未实现）
        # batch_review.py 需要 --input 参数指定 JSON 文件
        # 输入格式: {"sorted": [{"issue": 108, "pr": 112, "state": "open", "priority": "p0"}]}
        try:
            script_path = Path(__file__).parent.parent.parent / "gh-project-pr" / "scripts" / "batch_review.py"
            if script_path.exists():
                # 从 state 中获取 issue 结果，构建 batch_review 需要的输入
                issue_results = self.state_manager.state.issue_results or []
                sorted_items = []

                for result in issue_results:
                    # state.issue_results 在持久化后为 dict；测试/Mock 可能为 IssueResult
                    if isinstance(result, dict):
                        pr_number = result.get("pr_number")
                        issue_number = result.get("number")
                        status = result.get("status")
                        title = result.get("title") or ""
                    else:
                        pr_number = getattr(result, "pr_number", None)
                        issue_number = getattr(result, "number", None)
                        status = getattr(result, "status", None)
                        title = getattr(result, "title", "") or ""

                    if pr_number:
                        sorted_items.append({
                            "issue": issue_number,
                            "pr": pr_number,
                            "state": "open" if status == "completed" else "closed",
                            "priority": "p1",  # 默认优先级
                            "title": title,
                        })

                if not sorted_items:
                    self._log("   没有 PR 需要审查")
                    return {"merged": [], "failed": []}

                # 创建临时 JSON 文件作为输入
                import tempfile
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    suffix=".json",
                    delete=False,
                    encoding="utf-8"
                ) as f:
                    input_data = {"sorted": sorted_items}
                    json.dump(input_data, f, ensure_ascii=False)
                    input_file = f.name

                try:
                    args = [
                        "python3", str(script_path),
                        "--input", input_file,
                        "--auto-merge",
                        "--review-backend", "codex",
                    ]

                    result = subprocess.run(
                        args,
                        capture_output=True,
                        text=True,
                        timeout=3600,  # 1 hour
                    )

                    # 兼容旧版本 batch_review.py：不支持 --review-backend 时自动回退
                    if (
                        result.returncode != 0
                        and result.stderr
                        and "--review-backend" in result.stderr
                        and ("unrecognized arguments" in result.stderr or "unknown option" in result.stderr)
                    ):
                        self._log("   ⚠️ batch_review 不支持 --review-backend，回退到旧接口")
                        legacy_args = [
                            "python3", str(script_path),
                            "--input", input_file,
                            "--auto-merge",
                        ]
                        result = subprocess.run(
                            legacy_args,
                            capture_output=True,
                            text=True,
                            timeout=3600,  # 1 hour
                        )

                    if result.returncode == 0 or result.stdout:
                        # 解析 batch_review.py 的 JSON 输出
                        # 输出格式: {"results": [...], "summary": {...}}
                        output = json.loads(result.stdout) if result.stdout else {}
                        return self._convert_batch_review_output(output)
                    else:
                        if result.stderr:
                            self._log(f"   batch_review 错误: {result.stderr[:300]}")
                finally:
                    # 清理临时文件
                    import os
                    try:
                        os.unlink(input_file)
                    except OSError:
                        pass

        except json.JSONDecodeError as e:
            self._log(f"   JSON 解析失败: {e}")
        except Exception as e:
            self._log(f"   调用失败: {e}")

        return {"merged": [], "failed": []}

    def _convert_batch_review_output(self, output: dict) -> dict:
        """将 batch_review.py 输出转换为 autopilot 期望的格式"""
        # batch_review 输出: {"results": [{"issue": N, "pr": N, "status": "merged|failed", "error": ...}], "summary": {...}}
        # autopilot 期望: {"merged": [pr_numbers], "failed": [{"number": N, "error": "..."}]}
        merged = []
        failed = []

        for r in output.get("results", []):
            if r.get("status") == "merged":
                merged.append(r.get("pr"))
            elif r.get("status") == "failed":
                failed.append({
                    "number": r.get("pr"),
                    "error": r.get("error", "unknown error"),
                })

        return {"merged": merged, "failed": failed}

    # === 辅助方法 ===

    def _log(self, message: str, error: bool = False) -> None:
        """输出日志"""
        stream = sys.stderr if error else sys.stdout
        print(message, file=stream, flush=True)

    def _extract_title(self, content: str) -> str:
        """从内容中提取标题"""
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("# "):
                return line[2:].strip()
            if line.startswith("**") and "**" in line[2:]:
                return line.replace("**", "").strip()
        return content[:50]


def main():
    parser = argparse.ArgumentParser(
        description="gh-autopilot: 端到端自动化工作流",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 基于 PRD 文件启动
  python3 autopilot.py docs/feature-prd.md

  # 基于需求描述启动
  python3 autopilot.py "添加用户登录功能"

  # 预览模式
  python3 autopilot.py docs/feature-prd.md --dry-run

  # 跳过 PRD 生成
  python3 autopilot.py docs/feature-prd.md --skip-prd
""",
    )
    parser.add_argument(
        "input",
        help="PRD 文件路径或需求描述",
    )
    parser.add_argument(
        "--skip-prd",
        action="store_true",
        help="跳过 PRD 生成，直接创建 Issue",
    )
    parser.add_argument(
        "--skip-sync",
        action="store_true",
        help="跳过 Project 同步",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="预览模式，不执行实际操作",
    )
    parser.add_argument(
        "--project",
        type=int,
        help="指定已有 Project 编号",
    )
    parser.add_argument(
        "--priority",
        help="只处理指定优先级 (逗号分隔，如 p0,p1)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="详细输出",
    )

    args = parser.parse_args()

    autopilot = Autopilot(
        input_source=args.input,
        skip_prd=args.skip_prd,
        skip_sync=args.skip_sync,
        dry_run=args.dry_run,
        project_number=args.project,
        priority_filter=args.priority,
        verbose=args.verbose,
    )

    sys.exit(autopilot.run())


if __name__ == "__main__":  # pragma: no cover
    main()
