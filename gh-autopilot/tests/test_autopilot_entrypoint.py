#!/usr/bin/env python3
"""
autopilot.py 入口与未覆盖分支测试（提升覆盖率）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 添加 scripts 目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import autopilot as autopilot_mod  # noqa: E402
from state import Phase, ResumeInfo  # noqa: E402


def test_cli_main_invokes_autopilot_run(monkeypatch):
    fake = MagicMock()
    fake.run.return_value = 0
    captured: dict = {}

    def fake_autopilot(**kwargs):
        captured.update(kwargs)
        return fake

    monkeypatch.setattr(autopilot_mod, "Autopilot", fake_autopilot)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "autopilot.py",
            "input.md",
            "--dry-run",
            "--skip-prd",
            "--skip-sync",
            "--project",
            "3",
            "--priority",
            "p0,p1",
            "-v",
        ],
    )

    with pytest.raises(SystemExit) as e:
        autopilot_mod.main()

    assert e.value.code == 0
    assert captured["input_source"] == "input.md"
    assert captured["dry_run"] is True
    assert captured["skip_prd"] is True
    assert captured["skip_sync"] is True
    assert captured["project_number"] == 3
    assert captured["priority_filter"] == "p0,p1"
    assert captured["verbose"] is True
    fake.run.assert_called_once()


def test_run_resume_path_uses_resume_info_and_dry_run_short_circuit():
    ap = autopilot_mod.Autopilot(
        input_source="test",
        resume=True,
        resume_run_id="r1",
        dry_run=True,
        skip_prd=True,
    )

    resume_info = ResumeInfo(
        original_run_id="r1",
        resume_phase=Phase.PRD,
        last_successful_step="prd_read",
        completed_steps=[],
        context={},
    )

    ap.state_manager = MagicMock()
    ap.state_manager.resume_from_checkpoint.return_value = resume_info
    ap.state_manager.state = MagicMock(current_phase=Phase.PRD.value)

    ap._phase_1_requirements = MagicMock(return_value="PRD")
    ap._phase_2_create_issues = MagicMock(return_value=[1])
    ap._phase_3_sync_project = MagicMock(return_value=1)

    assert ap.run() == 0


def test_phase1_generate_prd_success(tmp_path):
    prd_path = tmp_path / "prd.md"
    prd_path.write_text("# Title\nBody\n", encoding="utf-8")

    ap = autopilot_mod.Autopilot(input_source="some requirement", skip_prd=False)
    ap.state_manager = MagicMock()
    ap.state_manager.state = MagicMock()

    ap._is_step_completed = MagicMock(return_value=False)
    ap._invoke_skill_prd = MagicMock(return_value=str(prd_path))

    content = ap._phase_1_requirements()
    assert "Title" in content
    ap.state_manager.set_prd_info.assert_called()


def test_fallback_create_issue_parses_issue_number_from_url():
    ap = autopilot_mod.Autopilot(input_source="test")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="https://github.com/o/r/issues/123\n", stderr="")
        assert ap._fallback_create_issue("# T\n") == [123]

