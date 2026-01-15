#!/usr/bin/env python3
"""
gh-autopilot 状态管理模块。

管理执行状态到 .claude/autopilot-state.json
"""

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional


class Phase(str, Enum):
    """执行阶段枚举"""
    INIT = "init"
    PRD = "prd"
    CREATE_ISSUE = "create_issue"
    PROJECT_SYNC = "project_sync"
    IMPLEMENT = "implement"
    PR_REVIEW = "pr_review"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class IssueResult:
    """单个 Issue 执行结果"""
    number: int
    title: str
    status: str  # success, failed, skipped
    pr_number: Optional[int] = None
    error: Optional[str] = None


@dataclass
class AutopilotState:
    """Autopilot 执行状态"""
    # 基础信息
    run_id: str = ""
    input_source: str = ""  # PRD 文件路径或需求描述
    start_time: str = ""
    end_time: str = ""

    # 当前阶段
    current_phase: str = Phase.INIT.value

    # PRD 信息
    prd_path: str = ""
    prd_title: str = ""

    # Issue 信息
    issues_created: list[int] = field(default_factory=list)
    epic_number: Optional[int] = None

    # Project 信息
    project_number: Optional[int] = None
    project_url: str = ""

    # 执行结果
    issue_results: list[dict] = field(default_factory=list)
    pr_results: list[dict] = field(default_factory=list)

    # 统计
    total_issues: int = 0
    success_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0

    # 错误信息
    last_error: str = ""
    retry_count: int = 0


class StateManager:
    """状态管理器"""

    DEFAULT_STATE_PATH = ".claude/autopilot-state.json"
    MAX_RETRIES = 3

    def __init__(self, state_path: Optional[str] = None):
        self.state_path = Path(state_path or self.DEFAULT_STATE_PATH)
        self.state: AutopilotState = AutopilotState()

    def init_state(self, input_source: str) -> AutopilotState:
        """初始化新的执行状态"""
        self.state = AutopilotState(
            run_id=datetime.now().strftime("%Y%m%d_%H%M%S"),
            input_source=input_source,
            start_time=datetime.now().isoformat(),
            current_phase=Phase.INIT.value,
        )
        self._save()
        return self.state

    def load_state(self) -> Optional[AutopilotState]:
        """加载现有状态"""
        if not self.state_path.exists():
            return None

        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.state = AutopilotState(**data)
            return self.state
        except (json.JSONDecodeError, TypeError) as e:
            return None

    def _save(self) -> None:
        """保存状态到文件"""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(asdict(self.state), f, ensure_ascii=False, indent=2)

    def update_phase(self, phase: Phase) -> None:
        """更新当前阶段"""
        self.state.current_phase = phase.value
        self._save()

    def set_prd_info(self, path: str, title: str) -> None:
        """设置 PRD 信息"""
        self.state.prd_path = path
        self.state.prd_title = title
        self._save()

    def set_issues(self, issue_numbers: list[int], epic_number: Optional[int] = None) -> None:
        """设置创建的 Issue"""
        self.state.issues_created = issue_numbers
        self.state.epic_number = epic_number
        self.state.total_issues = len(issue_numbers)
        self._save()

    def set_project(self, project_number: int, project_url: str = "") -> None:
        """设置 Project 信息"""
        self.state.project_number = project_number
        self.state.project_url = project_url
        self._save()

    def add_issue_result(self, result: IssueResult) -> None:
        """添加 Issue 执行结果"""
        self.state.issue_results.append(asdict(result))
        if result.status == "success":
            self.state.success_count += 1
        elif result.status == "failed":
            self.state.failed_count += 1
        else:
            self.state.skipped_count += 1
        self._save()

    def add_pr_result(self, pr_number: int, status: str, error: Optional[str] = None) -> None:
        """添加 PR 审查结果"""
        self.state.pr_results.append({
            "pr_number": pr_number,
            "status": status,
            "error": error,
        })
        self._save()

    def set_error(self, error: str) -> None:
        """记录错误"""
        self.state.last_error = error
        self.state.retry_count += 1
        self._save()

    def can_retry(self) -> bool:
        """检查是否可以重试"""
        return self.state.retry_count < self.MAX_RETRIES

    def complete(self, success: bool = True) -> None:
        """标记完成"""
        self.state.end_time = datetime.now().isoformat()
        self.state.current_phase = Phase.COMPLETED.value if success else Phase.FAILED.value
        self._save()

    def get_summary(self) -> dict:
        """获取执行摘要"""
        return {
            "run_id": self.state.run_id,
            "input": self.state.input_source,
            "duration": self._calculate_duration(),
            "phase": self.state.current_phase,
            "total_issues": self.state.total_issues,
            "success": self.state.success_count,
            "failed": self.state.failed_count,
            "skipped": self.state.skipped_count,
            "project_number": self.state.project_number,
        }

    def _calculate_duration(self) -> str:
        """计算执行时长"""
        if not self.state.start_time:
            return "N/A"

        start = datetime.fromisoformat(self.state.start_time)
        end = datetime.fromisoformat(self.state.end_time) if self.state.end_time else datetime.now()

        duration = end - start
        minutes, seconds = divmod(int(duration.total_seconds()), 60)
        hours, minutes = divmod(minutes, 60)

        if hours > 0:
            return f"{hours}h {minutes}m {seconds}s"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"

    def clear(self) -> None:
        """清除状态文件"""
        if self.state_path.exists():
            self.state_path.unlink()
        self.state = AutopilotState()


def get_state_manager(state_path: Optional[str] = None) -> StateManager:
    """获取状态管理器实例"""
    return StateManager(state_path)


if __name__ == "__main__":
    # 测试状态管理
    manager = StateManager("/tmp/test-autopilot-state.json")

    # 初始化
    manager.init_state("test-feature")
    print(f"Initialized: {manager.state.run_id}")

    # 更新阶段
    manager.update_phase(Phase.CREATE_ISSUE)
    print(f"Phase: {manager.state.current_phase}")

    # 设置 Issue
    manager.set_issues([1, 2, 3], epic_number=1)
    print(f"Issues: {manager.state.issues_created}")

    # 添加结果
    manager.add_issue_result(IssueResult(number=1, title="Test", status="success", pr_number=10))
    print(f"Results: {manager.state.issue_results}")

    # 完成
    manager.complete()
    print(f"Summary: {manager.get_summary()}")

    # 清理
    manager.clear()
