#!/usr/bin/env python3
"""
gh-autopilot 依赖脚本验证模块。

在执行前验证外部依赖脚本的存在性和可访问性。
支持验证：
- 依赖脚本文件存在性
- 可执行文件（codeagent-wrapper, gh, git）
- GitHub CLI 认证状态
"""

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class DependencyInfo:
    """依赖脚本信息"""
    name: str
    path: Path
    exists: bool
    is_executable: bool = False
    error: Optional[str] = None


@dataclass
class ExecutableInfo:
    """可执行文件信息"""
    name: str
    path: Optional[Path]
    available: bool
    error: Optional[str] = None


@dataclass
class AuthInfo:
    """认证状态信息"""
    name: str
    authenticated: bool
    output: str = ""
    error: Optional[str] = None


@dataclass
class ValidationResult:
    """验证结果"""
    success: bool
    dependencies: list[DependencyInfo] = field(default_factory=list)
    executables: list[ExecutableInfo] = field(default_factory=list)
    auth_checks: list[AuthInfo] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        if self.success:
            total = len(self.dependencies) + len(self.executables) + len(self.auth_checks)
            return f"All {total} validations passed successfully"
        return f"Validation failed: {len(self.missing)} missing, {len(self.errors)} errors"


class DependencyValidatorError(Exception):
    """依赖验证错误"""

    def __init__(self, message: str, missing: list[str] = None, errors: list[str] = None):
        super().__init__(message)
        self.missing = missing or []
        self.errors = errors or []


class DependencyValidator:
    """
    依赖脚本验证器。

    检查 gh-autopilot 所依赖的外部脚本是否存在且可访问。

    用法:
        validator = DependencyValidator()
        result = validator.validate_all()
        if not result.success:
            raise DependencyValidatorError(f"Missing: {result.missing}")
    """

    # 默认依赖脚本配置
    # 格式: (skill_name, script_name)
    DEFAULT_DEPENDENCIES = [
        ("gh-project-implement", "batch_executor.py"),
        ("gh-project-implement", "priority_batcher.py"),
        ("gh-project-implement", "get_project_issues.py"),
        ("gh-project-sync", "create_project.py"),
        ("gh-project-sync", "list_projects.py"),
        ("gh-project-sync", "sync_project.py"),
        ("gh-project-pr", "main.py"),
    ]

    # 必需的可执行文件
    REQUIRED_EXECUTABLES = [
        "codeagent-wrapper",
        "gh",
        "git",
    ]

    # 需要验证的认证命令
    AUTH_CHECKS = [
        ("gh", ["gh", "auth", "status"]),
    ]

    def __init__(
        self,
        skills_dir: Optional[Path] = None,
        dependencies: Optional[list[tuple[str, str]]] = None,
    ):
        """
        初始化验证器。

        Args:
            skills_dir: skills 目录路径，默认从当前脚本位置推断
            dependencies: 依赖列表，格式为 [(skill_name, script_name), ...]
        """
        self._skills_dir = skills_dir
        self._dependencies = dependencies or self.DEFAULT_DEPENDENCIES
        self._resolved_paths: dict[str, Path] = {}

    @property
    def skills_dir(self) -> Path:
        """获取 skills 目录路径"""
        if self._skills_dir is None:
            # 从当前脚本位置推断: scripts/ -> gh-autopilot/ -> skills/
            self._skills_dir = Path(__file__).parent.parent.parent
        return self._skills_dir

    def _get_fallback_paths(self, skill_name: str, script_name: str) -> list[Path]:
        """
        获取脚本的候选路径列表（按优先级排序）。

        Args:
            skill_name: 技能名称
            script_name: 脚本文件名

        Returns:
            候选路径列表
        """
        paths = []

        # 1. 相对于 skills 目录的标准路径
        paths.append(self.skills_dir / skill_name / "scripts" / script_name)

        # 2. 环境变量指定的路径
        env_key = f"GH_SKILL_{skill_name.upper().replace('-', '_')}_DIR"
        if env_path := os.environ.get(env_key):
            paths.append(Path(env_path) / "scripts" / script_name)

        # 3. 用户 home 目录下的 .claude/skills
        home_skills = Path.home() / ".claude" / "skills"
        if home_skills != self.skills_dir:
            paths.append(home_skills / skill_name / "scripts" / script_name)

        # 4. 符号链接解析后的路径
        primary_path = self.skills_dir / skill_name
        if primary_path.is_symlink():
            resolved = primary_path.resolve()
            paths.append(resolved / "scripts" / script_name)

        return paths

    def resolve_path(self, skill_name: str, script_name: str) -> Optional[Path]:
        """
        解析脚本的实际路径。

        Args:
            skill_name: 技能名称
            script_name: 脚本文件名

        Returns:
            找到的脚本路径，如果未找到返回 None
        """
        cache_key = f"{skill_name}/{script_name}"

        if cache_key in self._resolved_paths:
            return self._resolved_paths[cache_key]

        for path in self._get_fallback_paths(skill_name, script_name):
            if path.exists() and path.is_file():
                self._resolved_paths[cache_key] = path
                return path

        return None

    def validate_dependency(self, skill_name: str, script_name: str) -> DependencyInfo:
        """
        验证单个依赖脚本。

        Args:
            skill_name: 技能名称
            script_name: 脚本文件名

        Returns:
            DependencyInfo 对象
        """
        resolved_path = self.resolve_path(skill_name, script_name)

        if resolved_path is None:
            # 返回主要路径用于错误报告
            primary_path = self.skills_dir / skill_name / "scripts" / script_name
            return DependencyInfo(
                name=f"{skill_name}/{script_name}",
                path=primary_path,
                exists=False,
                is_executable=False,
                error=f"Script not found in any fallback paths",
            )

        # 检查是否可执行（对于 Python 脚本，检查是否可读即可）
        is_executable = os.access(resolved_path, os.R_OK)

        return DependencyInfo(
            name=f"{skill_name}/{script_name}",
            path=resolved_path,
            exists=True,
            is_executable=is_executable,
            error=None if is_executable else "Script is not readable",
        )

    def validate_executable(self, name: str) -> ExecutableInfo:
        """
        验证单个可执行文件。

        Args:
            name: 可执行文件名称

        Returns:
            ExecutableInfo 对象
        """
        exe_path = shutil.which(name)

        if exe_path is None:
            return ExecutableInfo(
                name=name,
                path=None,
                available=False,
                error=f"Executable '{name}' not found in PATH",
            )

        return ExecutableInfo(
            name=name,
            path=Path(exe_path),
            available=True,
            error=None,
        )

    def validate_executables(
        self, executables: Optional[list[str]] = None, fail_fast: bool = True
    ) -> tuple[list[ExecutableInfo], list[str], list[str]]:
        """
        验证所有必需的可执行文件。

        Args:
            executables: 可执行文件列表，默认使用 REQUIRED_EXECUTABLES
            fail_fast: 如果为 True，在第一个错误时立即抛出异常

        Returns:
            (ExecutableInfo列表, missing列表, errors列表)

        Raises:
            DependencyValidatorError: 当 fail_fast=True 且发现缺失可执行文件时
        """
        executables = executables or self.REQUIRED_EXECUTABLES
        results: list[ExecutableInfo] = []
        missing: list[str] = []
        errors: list[str] = []

        for exe_name in executables:
            info = self.validate_executable(exe_name)
            results.append(info)

            if not info.available:
                missing.append(f"executable:{exe_name}")
                error_msg = info.error or f"Executable '{exe_name}' not available"
                errors.append(error_msg)

                if fail_fast:
                    raise DependencyValidatorError(
                        error_msg,
                        missing=[f"executable:{exe_name}"],
                        errors=[error_msg],
                    )

        return results, missing, errors

    def validate_auth_status(
        self, auth_checks: Optional[list[tuple[str, list[str]]]] = None, fail_fast: bool = True
    ) -> tuple[list[AuthInfo], list[str], list[str]]:
        """
        验证认证状态。

        Args:
            auth_checks: 认证检查列表，格式为 [(name, command), ...]，默认使用 AUTH_CHECKS
            fail_fast: 如果为 True，在第一个错误时立即抛出异常

        Returns:
            (AuthInfo列表, missing列表, errors列表)

        Raises:
            DependencyValidatorError: 当 fail_fast=True 且认证失败时
        """
        auth_checks = auth_checks or self.AUTH_CHECKS
        results: list[AuthInfo] = []
        missing: list[str] = []
        errors: list[str] = []

        for name, command in auth_checks:
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )

                if result.returncode == 0:
                    results.append(AuthInfo(
                        name=name,
                        authenticated=True,
                        output=result.stdout.strip(),
                        error=None,
                    ))
                else:
                    error_output = result.stderr.strip() or result.stdout.strip()
                    error_msg = f"Authentication check failed for '{name}': {error_output}"
                    results.append(AuthInfo(
                        name=name,
                        authenticated=False,
                        output=error_output,
                        error=error_msg,
                    ))
                    missing.append(f"auth:{name}")
                    errors.append(error_msg)

                    if fail_fast:
                        raise DependencyValidatorError(
                            error_msg,
                            missing=[f"auth:{name}"],
                            errors=[error_msg],
                        )

            except subprocess.TimeoutExpired:
                error_msg = f"Authentication check timed out for '{name}'"
                results.append(AuthInfo(
                    name=name,
                    authenticated=False,
                    output="",
                    error=error_msg,
                ))
                missing.append(f"auth:{name}")
                errors.append(error_msg)

                if fail_fast:
                    raise DependencyValidatorError(
                        error_msg,
                        missing=[f"auth:{name}"],
                        errors=[error_msg],
                    )

            except FileNotFoundError:
                error_msg = f"Command not found for auth check '{name}': {command[0]}"
                results.append(AuthInfo(
                    name=name,
                    authenticated=False,
                    output="",
                    error=error_msg,
                ))
                missing.append(f"auth:{name}")
                errors.append(error_msg)

                if fail_fast:
                    raise DependencyValidatorError(
                        error_msg,
                        missing=[f"auth:{name}"],
                        errors=[error_msg],
                    )

        return results, missing, errors

    def validate_all(self, fail_fast: bool = True) -> ValidationResult:
        """
        验证所有依赖：脚本、可执行文件和认证状态。

        Args:
            fail_fast: 如果为 True，在第一个错误时立即抛出异常

        Returns:
            ValidationResult 对象

        Raises:
            DependencyValidatorError: 当 fail_fast=True 且发现任何验证失败时
        """
        dependencies: list[DependencyInfo] = []
        executables: list[ExecutableInfo] = []
        auth_checks: list[AuthInfo] = []
        missing: list[str] = []
        errors: list[str] = []

        # 1. 验证依赖脚本
        for skill_name, script_name in self._dependencies:
            info = self.validate_dependency(skill_name, script_name)
            dependencies.append(info)

            if not info.exists:
                missing.append(info.name)
                error_msg = f"Missing dependency: {info.name} (expected at {info.path})"
                errors.append(error_msg)

                if fail_fast:
                    raise DependencyValidatorError(
                        error_msg,
                        missing=[info.name],
                        errors=[error_msg],
                    )
            elif not info.is_executable:
                error_msg = f"Dependency not accessible: {info.name} - {info.error}"
                errors.append(error_msg)

                if fail_fast:
                    raise DependencyValidatorError(
                        error_msg,
                        missing=[],
                        errors=[error_msg],
                    )

        # 2. 验证可执行文件
        exe_results, exe_missing, exe_errors = self.validate_executables(fail_fast=fail_fast)
        executables.extend(exe_results)
        missing.extend(exe_missing)
        errors.extend(exe_errors)

        # 3. 验证认证状态
        auth_results, auth_missing, auth_errors = self.validate_auth_status(fail_fast=fail_fast)
        auth_checks.extend(auth_results)
        missing.extend(auth_missing)
        errors.extend(auth_errors)

        success = len(missing) == 0 and len(errors) == 0
        return ValidationResult(
            success=success,
            dependencies=dependencies,
            executables=executables,
            auth_checks=auth_checks,
            missing=missing,
            errors=errors,
        )

    def get_script_path(self, skill_name: str, script_name: str) -> Path:
        """
        获取脚本路径，如果不存在则抛出异常。

        Args:
            skill_name: 技能名称
            script_name: 脚本文件名

        Returns:
            脚本的绝对路径

        Raises:
            DependencyValidatorError: 当脚本不存在时
        """
        path = self.resolve_path(skill_name, script_name)
        if path is None:
            primary_path = self.skills_dir / skill_name / "scripts" / script_name
            raise DependencyValidatorError(
                f"Required script not found: {skill_name}/{script_name}",
                missing=[f"{skill_name}/{script_name}"],
                errors=[f"Expected at: {primary_path}"],
            )
        return path

    def print_status(self) -> None:
        """打印所有依赖的状态（用于调试）"""
        print(f"Skills directory: {self.skills_dir}")
        print(f"Dependencies ({len(self._dependencies)}):")

        for skill_name, script_name in self._dependencies:
            info = self.validate_dependency(skill_name, script_name)
            status = "✅" if info.exists and info.is_executable else "❌"
            print(f"  {status} {info.name}")
            if info.exists:
                print(f"      Path: {info.path}")
            else:
                print(f"      Error: {info.error}")


def get_validator(skills_dir: Optional[Path] = None) -> DependencyValidator:
    """获取验证器实例的便捷函数"""
    return DependencyValidator(skills_dir=skills_dir)


def validate_dependencies(fail_fast: bool = True) -> ValidationResult:
    """验证所有依赖的便捷函数"""
    validator = DependencyValidator()
    return validator.validate_all(fail_fast=fail_fast)


if __name__ == "__main__":
    # 测试验证器
    import sys

    validator = DependencyValidator()
    validator.print_status()

    print("\nValidation result:")
    try:
        result = validator.validate_all(fail_fast=False)
        print(f"  Success: {result.success}")
        print(f"  Dependencies: {len(result.dependencies)}")
        print(f"  Missing: {result.missing}")
        print(f"  Errors: {result.errors}")
        sys.exit(0 if result.success else 1)
    except DependencyValidatorError as e:
        print(f"  Error: {e}")
        sys.exit(1)
