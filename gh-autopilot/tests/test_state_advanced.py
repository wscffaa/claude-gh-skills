#!/usr/bin/env python3
"""
state.py 进阶分支覆盖测试（checkpoint/resume 等）。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# 添加 scripts 目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from state import (  # noqa: E402
    Checkpoint,
    ErrorRecord,
    Phase,
    ResumeInfo,
    StateManager,
)


def test_phase_helpers_get_next_and_resumable():
    assert Phase.get_next_phase(Phase.INIT) == Phase.PRD
    assert Phase.get_next_phase(Phase.COMPLETED) is None
    # invalid current phase should return None
    assert Phase.get_next_phase("nope") is None  # type: ignore[arg-type]

    assert Phase.is_resumable(Phase.INIT) is True
    assert Phase.is_resumable(Phase.COMPLETED) is False
    assert Phase.is_resumable(Phase.FAILED) is False


def test_checkpoint_and_error_record_from_dict():
    cp = Checkpoint.from_dict({"phase": "prd", "step": "read", "timestamp": "t", "context": {"a": 1}, "completed": True})
    assert cp.phase == "prd"
    assert cp.context["a"] == 1

    er = ErrorRecord.from_dict(
        {"phase": "prd", "step": "x", "timestamp": "t", "error_type": "E", "message": "m", "recoverable": False}
    )
    assert er.error_type == "E"
    assert er.recoverable is False


def test_resume_info_should_skip_and_context_value():
    info = ResumeInfo(
        original_run_id="r1",
        resume_phase=Phase.IMPLEMENT,
        last_successful_step="s",
        completed_steps=["a"],
        context={"k": "v"},
    )
    assert info.should_skip_phase(Phase.PRD) is True
    assert info.should_skip_phase(Phase.IMPLEMENT) is False
    assert info.get_context_value("k") == "v"
    assert info.get_context_value("missing", "d") == "d"


def test_deserialize_state_fills_defaults(tmp_path):
    manager = StateManager(str(tmp_path / "state.json"))
    state = manager._deserialize_state(
        {
            "run_id": "x",
            "input_source": "i",
            "start_time": "",
            "end_time": "",
            "current_phase": Phase.INIT.value,
        }
    )
    assert hasattr(state, "phase_checkpoints")
    assert hasattr(state, "test_results")


def test_checkpoint_and_get_checkpoint(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "checkpoints"
    checkpoint_dir.mkdir()
    monkeypatch.setattr(StateManager, "CHECKPOINT_DIR", checkpoint_dir)

    manager = StateManager(str(tmp_path / "state.json"))
    manager.init_state("input")
    manager.checkpoint(Phase.PRD, "prd_read", context={"prd_path": "x"})

    got = manager.get_checkpoint(Phase.PRD)
    assert got is not None
    assert got.step == "prd_read"


def test_record_error_and_resume_from_checkpoint(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "checkpoints"
    checkpoint_dir.mkdir()
    monkeypatch.setattr(StateManager, "CHECKPOINT_DIR", checkpoint_dir)

    manager = StateManager(str(tmp_path / "state.json"))
    state = manager.init_state("input")
    manager.update_phase(Phase.IMPLEMENT)
    manager.checkpoint(Phase.IMPLEMENT, "impl_done", context={"x": 1})

    manager.record_error(Phase.IMPLEMENT, "impl", Exception("boom"), recoverable=True)
    assert manager.state.error_history
    assert manager.state.retry_count >= 1

    original_run_id = state.run_id
    resume_info = manager.resume_from_checkpoint(run_id=original_run_id)
    assert resume_info is not None
    assert resume_info.original_run_id == original_run_id
    assert resume_info.resume_phase == Phase.IMPLEMENT
    assert manager.state.resumed_from == original_run_id
    assert manager.state.run_id.startswith(f"{original_run_id}_r")


def test_resume_from_checkpoint_missing_file_returns_none(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "checkpoints"
    checkpoint_dir.mkdir()
    monkeypatch.setattr(StateManager, "CHECKPOINT_DIR", checkpoint_dir)

    manager = StateManager(str(tmp_path / "state.json"))
    assert manager.resume_from_checkpoint(run_id="missing") is None


def test_get_resumable_runs_filters_and_sorts(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "checkpoints"
    checkpoint_dir.mkdir()
    monkeypatch.setattr(StateManager, "CHECKPOINT_DIR", checkpoint_dir)

    # resumable run
    run1 = {
        "run_id": "r1",
        "input_source": "a",
        "current_phase": Phase.IMPLEMENT.value,
        "last_successful_step": "x",
        "start_time": "2024-01-02T00:00:00",
        "error_history": [],
    }
    (checkpoint_dir / "r1.json").write_text(json.dumps(run1), encoding="utf-8")

    # non-resumable run (completed)
    run2 = {
        "run_id": "r2",
        "input_source": "b",
        "current_phase": Phase.COMPLETED.value,
        "start_time": "2024-01-03T00:00:00",
    }
    (checkpoint_dir / "r2.json").write_text(json.dumps(run2), encoding="utf-8")

    # invalid json should be skipped
    (checkpoint_dir / "bad.json").write_text("{not json", encoding="utf-8")

    manager = StateManager(str(tmp_path / "state.json"))
    runs = manager.get_resumable_runs()
    assert len(runs) == 1
    assert runs[0]["run_id"] == "r1"


def test_calculate_duration_branches(tmp_path):
    manager = StateManager(str(tmp_path / "state.json"))
    manager.state.start_time = ""
    assert manager._calculate_duration() == "N/A"

    start = datetime.now() - timedelta(minutes=2, seconds=5)
    manager.state.start_time = start.isoformat()
    manager.state.end_time = datetime.now().isoformat()
    # minutes 分支
    assert "m" in manager._calculate_duration()

