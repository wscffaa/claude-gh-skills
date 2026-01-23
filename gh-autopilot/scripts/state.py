#!/usr/bin/env python3
"""
gh-autopilot 状态管理模块。

管理执行状态到 .claude/autopilot-state.json
支持 checkpoint/resume 机制实现跨阶段状态持久化和恢复。
"""

import json
import os
import shutil
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, Any


class Phase(str, Enum):
    """执行阶段枚举"""
    INIT = "init"
    PRD = "prd"
    CREATE_ISSUE = "create_issue"
    PROJECT_SYNC = "project_sync"
    IMPLEMENT = "implement"
    TEST_RUN = "test_run"  # Phase 4.5: 自动测试运行
    PR_REVIEW = "pr_review"
    COMPLETED = "completed"
    FAILED = "failed"

    @classmethod
    def get_phase_order(cls) -> list["Phase"]:
        """获取阶段执行顺序"""
        return [cls.INIT, cls.PRD, cls.CREATE_ISSUE, cls.PROJECT_SYNC, cls.IMPLEMENT, cls.TEST_RUN, cls.PR_REVIEW, cls.COMPLETED]

    @classmethod
    def get_next_phase(cls, current: "Phase") -> Optional["Phase"]:
        """获取下一个阶段"""
        order = cls.get_phase_order()
        try:
            idx = order.index(current)
            if idx < len(order) - 1:
                return order[idx + 1]
        except ValueError:
            pass
        return None

    @classmethod
    def is_resumable(cls, phase: "Phase") -> bool:
        """判断阶段是否可恢复"""
        return phase not in (cls.COMPLETED, cls.FAILED)


@dataclass
class Checkpoint:
    """检查点数据结构"""
    phase: str
    step: str
    timestamp: str
    context: dict = field(default_factory=dict)
    completed: bool = False

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "step": self.step,
            "timestamp": self.timestamp,
            "context": self.context,
            "completed": self.completed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Checkpoint":
        return cls(
            phase=data.get("phase", ""),
            step=data.get("step", ""),
            timestamp=data.get("timestamp", ""),
            context=data.get("context", {}),
            completed=data.get("completed", False),
        )


@dataclass
class ErrorRecord:
    """错误记录"""
    phase: str
    step: str
    timestamp: str
    error_type: str
    message: str
    recoverable: bool = True

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "step": self.step,
            "timestamp": self.timestamp,
            "error_type": self.error_type,
            "message": self.message,
            "recoverable": self.recoverable,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ErrorRecord":
        return cls(
            phase=data.get("phase", ""),
            step=data.get("step", ""),
            timestamp=data.get("timestamp", ""),
            error_type=data.get("error_type", ""),
            message=data.get("message", ""),
            recoverable=data.get("recoverable", True),
        )


@dataclass
class ResumeInfo:
    """恢复信息"""
    original_run_id: str
    resume_phase: "Phase"
    last_successful_step: str
    completed_steps: list[str] = field(default_factory=list)
    context: dict = field(default_factory=dict)

    def should_skip_phase(self, phase: "Phase") -> bool:
        """判断是否应跳过某个阶段（已完成）"""
        phase_order = Phase.get_phase_order()
        try:
            resume_idx = phase_order.index(self.resume_phase)
            check_idx = phase_order.index(phase)
            return check_idx < resume_idx
        except ValueError:
            return False

    def get_context_value(self, key: str, default: Any = None) -> Any:
        """获取上下文中的值"""
        return self.context.get(key, default)


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

    # 测试结果 (Phase 4.5)
    # 格式: [{command, status, output, duration, return_code, timestamp}, ...]
    test_results: list[dict] = field(default_factory=list)

    # 错误信息
    last_error: str = ""
    retry_count: int = 0

    # === Checkpoint/Resume 新增字段 ===
    # 阶段检查点映射: {phase: Checkpoint}
    phase_checkpoints: dict = field(default_factory=dict)

    # 最后成功的步骤
    last_successful_step: str = ""
    last_successful_phase: str = ""

    # 错误历史
    error_history: list[dict] = field(default_factory=list)

    # 已完成的步骤集合 (用于幂等性检查)
    completed_steps: list[str] = field(default_factory=list)

    # Resume 元数据
    resumed_from: str = ""  # 从哪个 run_id 恢复
    resume_count: int = 0  # 恢复次数


class StateManager:
    """状态管理器"""

    DEFAULT_STATE_PATH = ".claude/autopilot-state.json"
    CHECKPOINT_DIR = Path.home() / ".cache" / "gh-autopilot" / "state"
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
        self._save_checkpoint_file()
        return self.state

    def load_state(self) -> Optional[AutopilotState]:
        """加载现有状态"""
        if not self.state_path.exists():
            return None

        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.state = self._deserialize_state(data)
            return self.state
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            return None

    def _deserialize_state(self, data: dict) -> AutopilotState:
        """反序列化状态数据，处理兼容性"""
        # 处理新增字段的默认值
        defaults = {
            "phase_checkpoints": {},
            "last_successful_step": "",
            "last_successful_phase": "",
            "error_history": [],
            "completed_steps": [],
            "resumed_from": "",
            "resume_count": 0,
            "test_results": [],  # Phase 4.5: 测试结果
        }
        for key, default_val in defaults.items():
            if key not in data:
                data[key] = default_val
        return AutopilotState(**data)

    def _save(self) -> None:
        """保存状态到文件"""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(asdict(self.state), f, ensure_ascii=False, indent=2)

    def _save_checkpoint_file(self) -> None:
        """保存状态到 checkpoint 目录"""
        if not self.state.run_id:
            return
        self.CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        checkpoint_path = self.CHECKPOINT_DIR / f"{self.state.run_id}.json"
        with open(checkpoint_path, "w", encoding="utf-8") as f:
            json.dump(asdict(self.state), f, ensure_ascii=False, indent=2)

    def checkpoint(
        self,
        phase: Phase,
        step: str,
        context: Optional[dict] = None,
        completed: bool = True,
    ) -> Checkpoint:
        """
        保存检查点。

        在每个成功步骤后调用，记录当前状态以支持恢复。

        Args:
            phase: 当前阶段
            step: 步骤标识符 (如 "prd_read", "issue_create_1")
            context: 可选的上下文数据
            completed: 步骤是否完成

        Returns:
            创建的 Checkpoint 对象
        """
        checkpoint = Checkpoint(
            phase=phase.value,
            step=step,
            timestamp=datetime.now().isoformat(),
            context=context or {},
            completed=completed,
        )

        # 更新阶段检查点
        self.state.phase_checkpoints[phase.value] = checkpoint.to_dict()

        # 更新最后成功的步骤
        if completed:
            self.state.last_successful_phase = phase.value
            self.state.last_successful_step = step

            # 添加到已完成步骤列表 (用于幂等性)
            step_key = f"{phase.value}:{step}"
            if step_key not in self.state.completed_steps:
                self.state.completed_steps.append(step_key)

        self._save()
        self._save_checkpoint_file()

        return checkpoint

    def is_step_completed(self, phase: Phase, step: str) -> bool:
        """
        检查步骤是否已完成（幂等性检查）。

        Args:
            phase: 阶段
            step: 步骤标识符

        Returns:
            True 如果步骤已完成
        """
        step_key = f"{phase.value}:{step}"
        return step_key in self.state.completed_steps

    def get_checkpoint(self, phase: Phase) -> Optional[Checkpoint]:
        """
        获取指定阶段的检查点。

        Args:
            phase: 阶段

        Returns:
            Checkpoint 对象，如果不存在则返回 None
        """
        checkpoint_data = self.state.phase_checkpoints.get(phase.value)
        if checkpoint_data:
            return Checkpoint.from_dict(checkpoint_data)
        return None

    def record_error(
        self,
        phase: Phase,
        step: str,
        error: Exception,
        recoverable: bool = True,
    ) -> ErrorRecord:
        """
        记录错误到历史。

        Args:
            phase: 发生错误的阶段
            step: 发生错误的步骤
            error: 异常对象
            recoverable: 是否可恢复

        Returns:
            创建的 ErrorRecord 对象
        """
        error_record = ErrorRecord(
            phase=phase.value,
            step=step,
            timestamp=datetime.now().isoformat(),
            error_type=type(error).__name__,
            message=str(error),
            recoverable=recoverable,
        )

        self.state.error_history.append(error_record.to_dict())
        self.state.last_error = str(error)
        self.state.retry_count += 1

        self._save()
        self._save_checkpoint_file()

        return error_record

    def resume_from_checkpoint(self, run_id: Optional[str] = None) -> Optional["ResumeInfo"]:
        """
        从检查点恢复状态。

        Args:
            run_id: 可选的 run_id，如果不指定则尝试从当前状态文件恢复

        Returns:
            ResumeInfo 对象包含恢复信息，如果无法恢复则返回 None
        """
        # 确定要恢复的状态
        if run_id:
            # 从 checkpoint 目录加载指定的 run
            checkpoint_path = self.CHECKPOINT_DIR / f"{run_id}.json"
            if not checkpoint_path.exists():
                return None
            try:
                with open(checkpoint_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.state = self._deserialize_state(data)
            except (json.JSONDecodeError, TypeError, KeyError):
                return None
        else:
            # 尝试从当前状态文件恢复
            if not self.load_state():
                return None

        # 检查状态是否可恢复
        current_phase = Phase(self.state.current_phase)
        if not Phase.is_resumable(current_phase):
            return None

        # 创建 ResumeInfo
        resume_info = ResumeInfo(
            original_run_id=self.state.run_id,
            resume_phase=current_phase,
            last_successful_step=self.state.last_successful_step,
            completed_steps=list(self.state.completed_steps),
            context=self._get_resume_context(),
        )

        # 更新恢复元数据
        original_run_id = self.state.run_id
        self.state.resumed_from = original_run_id
        self.state.resume_count += 1
        # 生成新的 run_id 以区分本次运行
        self.state.run_id = f"{original_run_id}_r{self.state.resume_count}"

        self._save()
        self._save_checkpoint_file()

        return resume_info

    def _get_resume_context(self) -> dict:
        """获取恢复所需的上下文数据"""
        return {
            "prd_path": self.state.prd_path,
            "prd_title": self.state.prd_title,
            "issues_created": self.state.issues_created,
            "epic_number": self.state.epic_number,
            "project_number": self.state.project_number,
            "project_url": self.state.project_url,
            "issue_results": self.state.issue_results,
            "pr_results": self.state.pr_results,
        }

    def get_resumable_runs(self) -> list[dict]:
        """
        获取所有可恢复的运行记录。

        Returns:
            可恢复运行的列表
        """
        runs = []
        if not self.CHECKPOINT_DIR.exists():
            return runs

        for checkpoint_file in self.CHECKPOINT_DIR.glob("*.json"):
            try:
                with open(checkpoint_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                phase = Phase(data.get("current_phase", Phase.INIT.value))
                if Phase.is_resumable(phase):
                    runs.append({
                        "run_id": data.get("run_id", ""),
                        "input_source": data.get("input_source", ""),
                        "current_phase": phase.value,
                        "last_successful_step": data.get("last_successful_step", ""),
                        "start_time": data.get("start_time", ""),
                        "error_count": len(data.get("error_history", [])),
                    })
            except (json.JSONDecodeError, ValueError):
                continue

        # 按时间倒序排列
        runs.sort(key=lambda x: x.get("start_time", ""), reverse=True)
        return runs

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


if __name__ == "__main__":  # pragma: no cover
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
