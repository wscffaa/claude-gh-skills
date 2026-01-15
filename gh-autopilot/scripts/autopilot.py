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
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

from state import StateManager, Phase, IssueResult, get_state_manager
from report import ReportGenerator, ReportConfig


class AutopilotError(Exception):
    """Autopilot 执行错误"""
    pass


class Autopilot:
    """Autopilot 主执行器"""

    MAX_RETRIES = 3
    RETRY_DELAY = 5  # seconds

    def __init__(
        self,
        input_source: str,
        skip_prd: bool = False,
        skip_sync: bool = False,
        dry_run: bool = False,
        project_number: Optional[int] = None,
        priority_filter: Optional[str] = None,
        verbose: bool = False,
    ):
        self.input_source = input_source
        self.skip_prd = skip_prd
        self.skip_sync = skip_sync
        self.dry_run = dry_run
        self.project_number = project_number
        self.priority_filter = priority_filter
        self.verbose = verbose

        self.state_manager = get_state_manager()

    def run(self) -> int:
        """执行完整流程"""
        try:
            # 初始化状态
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

            # 阶段 5: 批量审查
            self._phase_5_review(project_number)

            # 阶段 6: 完成报告
            self._phase_6_report()

            return 0

        except AutopilotError as e:
            self._log(f"\n❌ 执行失败: {e}", error=True)
            self.state_manager.set_error(str(e))
            self.state_manager.complete(success=False)
            return 1

        except KeyboardInterrupt:
            self._log("\n⚠️ 用户中断", error=True)
            self.state_manager.set_error("用户中断")
            self.state_manager.complete(success=False)
            return 130

        except Exception as e:
            self._log(f"\n❌ 未知错误: {e}", error=True)
            self.state_manager.set_error(str(e))
            self.state_manager.complete(success=False)
            return 1

    def _phase_1_requirements(self) -> str:
        """阶段 1: 需求确认"""
        self._log("\n🔍 阶段 1/6: 需求确认...")
        self.state_manager.update_phase(Phase.PRD)

        # 检查输入是否为文件
        input_path = Path(self.input_source)
        if input_path.exists() and input_path.suffix in (".md", ".txt"):
            self._log(f"   读取 PRD 文件: {self.input_source}")
            prd_content = input_path.read_text(encoding="utf-8")
            self.state_manager.set_prd_info(str(input_path), self._extract_title(prd_content))
            return prd_content

        # 输入为需求描述
        if self.skip_prd:
            self._log("   跳过 PRD 生成，使用原始需求描述")
            self.state_manager.set_prd_info("", self.input_source[:50])
            return self.input_source

        # 调用 /product-requirements 生成 PRD
        self._log("   调用 /product-requirements 生成 PRD...")
        prd_path = self._invoke_skill_prd(self.input_source)
        if prd_path and Path(prd_path).exists():
            prd_content = Path(prd_path).read_text(encoding="utf-8")
            self.state_manager.set_prd_info(prd_path, self._extract_title(prd_content))
            return prd_content

        # PRD 生成失败，使用原始描述
        self._log("   ⚠️ PRD 生成失败，使用原始需求描述")
        self.state_manager.set_prd_info("", self.input_source[:50])
        return self.input_source

    def _phase_2_create_issues(self, prd_content: str) -> list[int]:
        """阶段 2: 创建 Issue"""
        self._log("\n📝 阶段 2/6: 创建 Issue...")
        self.state_manager.update_phase(Phase.CREATE_ISSUE)

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                issues = self._invoke_skill_create_issue(prd_content)
                if issues:
                    self._log(f"   ✅ 创建了 {len(issues)} 个 Issue")
                    self.state_manager.set_issues(issues)
                    return issues
            except Exception as e:
                self._log(f"   ⚠️ 尝试 {attempt}/{self.MAX_RETRIES} 失败: {e}")
                if attempt < self.MAX_RETRIES:
                    time.sleep(self.RETRY_DELAY)

        return []

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

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                project_number = self._invoke_skill_project_sync()
                if project_number:
                    self._log(f"   ✅ 同步到 Project #{project_number}")
                    self.state_manager.set_project(project_number)
                    return project_number
            except Exception as e:
                self._log(f"   ⚠️ 尝试 {attempt}/{self.MAX_RETRIES} 失败: {e}")
                if attempt < self.MAX_RETRIES:
                    time.sleep(self.RETRY_DELAY)

        # 同步失败，非关键路径，继续执行
        self._log("   ⚠️ Project 同步失败，继续执行...")
        return self.project_number or 1

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
                result = subprocess.run(
                    ["python3", str(script_path), "--json"],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode == 0:
                    data = json.loads(result.stdout)
                    return data.get("project_number")
        except Exception as e:
            self._log(f"   调用失败: {e}")

        return 1  # 默认 Project 1

    def _invoke_skill_project_implement(self, project_number: int) -> dict:
        """调用 /gh-project-implement"""
        self._log(f"   (调用 /gh-project-implement {project_number})")

        # 实际调用 gh-project-implement 脚本
        try:
            script_path = Path(__file__).parent.parent.parent / "gh-project-implement" / "scripts" / "batch_executor.py"
            if script_path.exists():
                args = ["python3", str(script_path), "--project", str(project_number), "--json"]
                if self.priority_filter:
                    args.extend(["--priority", self.priority_filter])

                result = subprocess.run(
                    args,
                    capture_output=True,
                    text=True,
                    timeout=7200,  # 2 hours
                )
                if result.returncode == 0:
                    return json.loads(result.stdout)
        except Exception as e:
            self._log(f"   调用失败: {e}")

        return {"results": []}

    def _invoke_skill_project_pr(self, project_number: int) -> dict:
        """调用 /gh-project-pr"""
        self._log(f"   (调用 /gh-project-pr {project_number} --auto-merge)")

        # 实际调用 gh-project-pr 脚本
        try:
            script_path = Path(__file__).parent.parent.parent / "gh-project-pr" / "scripts" / "main.py"
            if script_path.exists():
                args = ["python3", str(script_path), "--project", str(project_number), "--auto-merge", "--json"]
                if self.priority_filter:
                    args.extend(["--priority", self.priority_filter])

                result = subprocess.run(
                    args,
                    capture_output=True,
                    text=True,
                    timeout=3600,  # 1 hour
                )
                if result.returncode == 0:
                    return json.loads(result.stdout)
        except Exception as e:
            self._log(f"   调用失败: {e}")

        return {"merged": [], "failed": []}

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


if __name__ == "__main__":
    main()
