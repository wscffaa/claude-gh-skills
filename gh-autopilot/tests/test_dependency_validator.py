#!/usr/bin/env python3
"""
dependency_validator.py 单元测试（覆盖率补齐）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 添加 scripts 目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from dependency_validator import (  # noqa: E402
    AuthInfo,
    DependencyInfo,
    DependencyValidator,
    DependencyValidatorError,
    ExecutableInfo,
    ValidationResult,
    get_validator,
    validate_dependencies,
)


def test_validation_result_str_success_and_failure():
    ok = ValidationResult(
        success=True,
        dependencies=[DependencyInfo(name="a", path=Path("/tmp/a"), exists=True)],
        executables=[ExecutableInfo(name="gh", path=Path("/usr/bin/gh"), available=True)],
        auth_checks=[AuthInfo(name="gh", authenticated=True)],
    )
    assert "validations passed" in str(ok)

    bad = ValidationResult(success=False, missing=["x"], errors=["err"])
    assert "Validation failed" in str(bad)


def test_dependency_validator_error_defaults():
    err = DependencyValidatorError("boom")
    assert err.missing == []
    assert err.errors == []

    err2 = DependencyValidatorError("boom", missing=["a"], errors=["e"])
    assert err2.missing == ["a"]
    assert err2.errors == ["e"]


def test_skills_dir_default_inference():
    validator = DependencyValidator(dependencies=[])
    skills_dir = validator.skills_dir
    assert isinstance(skills_dir, Path)


def test_get_fallback_paths_includes_env_home_and_symlink(tmp_path, monkeypatch):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    skill_name = "gh-project-sync"
    script_name = "sync_project.py"

    # 1) symlink path
    real_skill = tmp_path / "real-skill"
    (real_skill / "scripts").mkdir(parents=True)
    (real_skill / "scripts" / script_name).write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    (skills_dir / skill_name).symlink_to(real_skill, target_is_directory=True)

    # 2) env var fallback
    env_skill = tmp_path / "env-skill"
    (env_skill / "scripts").mkdir(parents=True)
    (env_skill / "scripts" / script_name).write_text("print('x')\n", encoding="utf-8")
    monkeypatch.setenv("GH_SKILL_GH_PROJECT_SYNC_DIR", str(env_skill))

    # 3) home ~/.claude/skills fallback
    fake_home = tmp_path / "home"
    home_script = fake_home / ".claude" / "skills" / skill_name / "scripts" / script_name
    home_script.parent.mkdir(parents=True)
    home_script.write_text("print('home')\n", encoding="utf-8")
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)

    validator = DependencyValidator(skills_dir=skills_dir, dependencies=[])
    paths = validator._get_fallback_paths(skill_name, script_name)

    assert skills_dir / skill_name / "scripts" / script_name in paths
    assert env_skill / "scripts" / script_name in paths
    assert home_script in paths
    assert real_skill / "scripts" / script_name in paths


def test_resolve_path_caches_result(tmp_path, monkeypatch):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    skill_name = "gh-project-sync"
    script_name = "sync_project.py"

    env_skill = tmp_path / "env-skill"
    (env_skill / "scripts").mkdir(parents=True)
    script_path = env_skill / "scripts" / script_name
    script_path.write_text("print('x')\n", encoding="utf-8")

    monkeypatch.setenv("GH_SKILL_GH_PROJECT_SYNC_DIR", str(env_skill))

    validator = DependencyValidator(skills_dir=skills_dir, dependencies=[])
    resolved1 = validator.resolve_path(skill_name, script_name)
    assert resolved1 == script_path

    # 删除文件后再次调用应命中缓存
    script_path.unlink()
    resolved2 = validator.resolve_path(skill_name, script_name)
    assert resolved2 == script_path


def test_validate_dependency_not_found_returns_primary_path(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    validator = DependencyValidator(skills_dir=skills_dir, dependencies=[])
    info = validator.validate_dependency("x-skill", "missing.py")

    assert info.exists is False
    assert info.path == skills_dir / "x-skill" / "scripts" / "missing.py"
    assert info.error


def test_validate_dependency_found_not_readable(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    script_path = skills_dir / "skill" / "scripts" / "ok.py"
    script_path.parent.mkdir(parents=True)
    script_path.write_text("print('ok')\n", encoding="utf-8")

    validator = DependencyValidator(skills_dir=skills_dir, dependencies=[])

    with patch("os.access", return_value=False):
        info = validator.validate_dependency("skill", "ok.py")

    assert info.exists is True
    assert info.is_executable is False
    assert info.error == "Script is not readable"


def test_validate_executable_missing_and_found(monkeypatch):
    validator = DependencyValidator(dependencies=[])

    monkeypatch.setattr("shutil.which", lambda name: None)
    missing = validator.validate_executable("gh")
    assert missing.available is False
    assert missing.error

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/gh")
    ok = validator.validate_executable("gh")
    assert ok.available is True
    assert ok.path == Path("/usr/bin/gh")


def test_validate_executables_fail_fast_raises(monkeypatch):
    validator = DependencyValidator(dependencies=[])

    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(DependencyValidatorError):
        validator.validate_executables(executables=["gh"], fail_fast=True)


def test_validate_executables_collects_errors_when_not_fail_fast(monkeypatch):
    validator = DependencyValidator(dependencies=[])

    def fake_which(name: str):
        return "/usr/bin/git" if name == "git" else None

    monkeypatch.setattr("shutil.which", fake_which)
    results, missing, errors = validator.validate_executables(executables=["gh", "git"], fail_fast=False)

    assert len(results) == 2
    assert "executable:gh" in missing
    assert errors


def test_validate_auth_status_success_and_failure_fail_fast_false(monkeypatch):
    validator = DependencyValidator(dependencies=[])

    def fake_run(cmd, capture_output, text, timeout):
        if cmd[0] == "gh":
            return MagicMock(returncode=0, stdout="ok\n", stderr="")
        return MagicMock(returncode=1, stdout="", stderr="no auth")

    monkeypatch.setattr("subprocess.run", fake_run)
    results, missing, errors = validator.validate_auth_status(
        auth_checks=[("gh", ["gh", "auth", "status"]), ("x", ["x", "auth"])],
        fail_fast=False,
    )

    assert [r.authenticated for r in results] == [True, False]
    assert missing == ["auth:x"]
    assert errors and "Authentication check failed" in errors[0]


def test_validate_auth_status_timeout_and_command_not_found(monkeypatch):
    import subprocess

    validator = DependencyValidator(dependencies=[])

    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired(cmd=["gh"], timeout=30)),
    )
    results, missing, errors = validator.validate_auth_status(
        auth_checks=[("gh", ["gh", "auth", "status"])],
        fail_fast=False,
    )
    assert results[0].authenticated is False
    assert missing == ["auth:gh"]
    assert errors

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError()))
    results, missing, errors = validator.validate_auth_status(
        auth_checks=[("gh", ["gh", "auth", "status"])],
        fail_fast=False,
    )
    assert results[0].authenticated is False
    assert missing == ["auth:gh"]
    assert errors and "Command not found" in errors[0]


def test_validate_all_collects_missing_dependency(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    validator = DependencyValidator(
        skills_dir=skills_dir,
        dependencies=[("some-skill", "missing.py")],
    )

    with patch.object(DependencyValidator, "validate_executables", return_value=([], [], [])):
        with patch.object(DependencyValidator, "validate_auth_status", return_value=([], [], [])):
            result = validator.validate_all(fail_fast=False)

    assert result.success is False
    assert "some-skill/missing.py" in result.missing


def test_validate_all_dependency_not_accessible(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    script_path = skills_dir / "some-skill" / "scripts" / "ok.py"
    script_path.parent.mkdir(parents=True)
    script_path.write_text("print('ok')\n", encoding="utf-8")

    validator = DependencyValidator(
        skills_dir=skills_dir,
        dependencies=[("some-skill", "ok.py")],
    )

    with patch("os.access", return_value=False):
        with patch.object(DependencyValidator, "validate_executables", return_value=([], [], [])):
            with patch.object(DependencyValidator, "validate_auth_status", return_value=([], [], [])):
                result = validator.validate_all(fail_fast=False)

    assert result.success is False
    assert result.errors and "Dependency not accessible" in result.errors[0]


def test_get_script_path_raises_and_returns(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    validator = DependencyValidator(skills_dir=skills_dir, dependencies=[])

    with pytest.raises(DependencyValidatorError):
        validator.get_script_path("skill", "missing.py")

    script_path = skills_dir / "skill" / "scripts" / "ok.py"
    script_path.parent.mkdir(parents=True)
    script_path.write_text("print('ok')\n", encoding="utf-8")

    assert validator.get_script_path("skill", "ok.py") == script_path


def test_print_status_outputs(tmp_path, capsys):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    validator = DependencyValidator(skills_dir=skills_dir, dependencies=[("skill", "x.py")])

    # 避免依赖真实文件系统，直接 mock validate_dependency
    with patch.object(
        DependencyValidator,
        "validate_dependency",
        return_value=DependencyInfo(name="skill/x.py", path=Path("/tmp/x.py"), exists=False, error="missing"),
    ):
        validator.print_status()

    out = capsys.readouterr().out
    assert "Skills directory" in out
    assert "Dependencies" in out


def test_get_validator_and_validate_dependencies_smoke(tmp_path):
    v = get_validator(skills_dir=tmp_path)
    assert isinstance(v, DependencyValidator)

    with patch(
        "dependency_validator.DependencyValidator.validate_all",
        return_value=ValidationResult(success=True),
    ):
        result = validate_dependencies(fail_fast=False)
    assert result.success is True

