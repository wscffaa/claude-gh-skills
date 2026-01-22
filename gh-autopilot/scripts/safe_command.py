#!/usr/bin/env python3
"""
安全命令构造工具模块。

提供用于安全构造和执行 shell 命令的工具函数，避免：
- heredoc 格式导致的 zsh eval 错误
- 特殊字符（引号、换行符、反斜杠、$、&、|、; 等）处理问题
- shell 注入风险

用法:
    from safe_command import SafeCommandBuilder, run_command_with_stdin

    # 使用 SafeCommandBuilder 构造命令
    builder = SafeCommandBuilder("codeagent-wrapper")
    builder.add_arg("--backend", "codex")
    builder.add_flag("-")
    cmd = builder.build()

    # 使用 run_command_with_stdin 安全执行
    result = run_command_with_stdin(cmd, stdin_content="任务描述", cwd=worktree_path)
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Optional, Union


# 需要在 shell 中转义的特殊字符
SHELL_SPECIAL_CHARS = frozenset('&|;<>()$`\\"\' \t\n*?[#~=%!')


@dataclass
class CommandResult:
    """命令执行结果"""
    returncode: int
    stdout: str
    stderr: str
    command: list[str]

    @property
    def success(self) -> bool:
        return self.returncode == 0

    def __bool__(self) -> bool:
        return self.success


class SafeCommandBuilder:
    """
    安全的命令构造器。

    使用列表模式构造命令，避免 shell 解析问题。

    Example:
        builder = SafeCommandBuilder("git")
        builder.add_arg("commit", "-m", "fix: 修复 bug")
        cmd = builder.build()
        # ['git', 'commit', '-m', 'fix: 修复 bug']
    """

    def __init__(self, executable: str):
        """
        初始化命令构造器。

        Args:
            executable: 可执行文件名或路径
        """
        self._executable = executable
        self._args: list[str] = []
        self._env_vars: dict[str, str] = {}

    def add_arg(self, *args: str) -> "SafeCommandBuilder":
        """
        添加命令参数。

        Args:
            *args: 一个或多个参数

        Returns:
            self，支持链式调用
        """
        for arg in args:
            if arg is not None:
                self._args.append(str(arg))
        return self

    def add_flag(self, flag: str) -> "SafeCommandBuilder":
        """
        添加标志参数（如 -v, --verbose, -）。

        Args:
            flag: 标志参数

        Returns:
            self，支持链式调用
        """
        if flag is not None:
            self._args.append(str(flag))
        return self

    def add_option(self, name: str, value: Optional[str]) -> "SafeCommandBuilder":
        """
        添加选项参数（如 --backend codex）。

        如果 value 为 None，则不添加该选项。

        Args:
            name: 选项名（如 --backend）
            value: 选项值

        Returns:
            self，支持链式调用
        """
        if value is not None:
            self._args.append(str(name))
            self._args.append(str(value))
        return self

    def add_env(self, key: str, value: str) -> "SafeCommandBuilder":
        """
        添加环境变量。

        Args:
            key: 环境变量名
            value: 环境变量值

        Returns:
            self，支持链式调用
        """
        self._env_vars[key] = value
        return self

    def build(self) -> list[str]:
        """
        构建命令列表。

        Returns:
            命令参数列表，可直接传给 subprocess
        """
        return [self._executable] + self._args

    def build_env(self) -> Optional[dict[str, str]]:
        """
        构建环境变量字典。

        Returns:
            合并后的环境变量字典，如果没有自定义环境变量则返回 None
        """
        if not self._env_vars:
            return None
        env = os.environ.copy()
        env.update(self._env_vars)
        return env

    def to_shell_string(self) -> str:
        """
        转换为 shell 安全的字符串表示（用于日志/调试）。

        使用 shlex.quote 确保每个参数正确转义。

        Returns:
            可安全在 shell 中执行的命令字符串
        """
        cmd = self.build()
        return " ".join(shlex.quote(arg) for arg in cmd)

    def __repr__(self) -> str:
        return f"SafeCommandBuilder({self.to_shell_string()})"


def quote_arg(arg: str) -> str:
    """
    使用 shlex.quote 安全转义单个参数。

    Args:
        arg: 要转义的参数

    Returns:
        转义后的参数
    """
    return shlex.quote(arg)


def quote_args(*args: str) -> list[str]:
    """
    使用 shlex.quote 安全转义多个参数。

    Args:
        *args: 要转义的参数列表

    Returns:
        转义后的参数列表
    """
    return [shlex.quote(arg) for arg in args]


def needs_escaping(s: str) -> bool:
    """
    检查字符串是否包含需要转义的特殊字符。

    Args:
        s: 要检查的字符串

    Returns:
        如果包含特殊字符返回 True
    """
    return any(c in SHELL_SPECIAL_CHARS for c in s)


def escape_for_logging(s: str, max_length: int = 200) -> str:
    """
    转义字符串用于日志输出，截断过长内容。

    Args:
        s: 要转义的字符串
        max_length: 最大长度

    Returns:
        安全的日志字符串
    """
    # 替换控制字符
    safe = s.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    if len(safe) > max_length:
        safe = safe[:max_length] + "..."
    return safe


def run_command_with_stdin(
    cmd: list[str],
    stdin_content: Optional[str] = None,
    cwd: Optional[Union[str, Path]] = None,
    timeout: Optional[float] = None,
    env: Optional[dict[str, str]] = None,
    capture_output: bool = True,
) -> CommandResult:
    """
    安全执行命令，通过 stdin 传递内容（避免 heredoc）。

    使用 subprocess.Popen 的 stdin=PIPE 模式，确保：
    - 不经过 shell 解析
    - 特殊字符不会被解释
    - 支持任意内容（包括二进制）

    Args:
        cmd: 命令列表（不是字符串！）
        stdin_content: 要传递给 stdin 的内容
        cwd: 工作目录
        timeout: 超时时间（秒）
        env: 环境变量
        capture_output: 是否捕获输出

    Returns:
        CommandResult 对象

    Raises:
        subprocess.TimeoutExpired: 超时
        FileNotFoundError: 命令不存在
    """
    if isinstance(cwd, Path):
        cwd = str(cwd)

    kwargs: dict[str, Any] = {
        "cwd": cwd,
        "text": True,
        "env": env,
    }

    if capture_output:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE

    if stdin_content is not None:
        kwargs["stdin"] = subprocess.PIPE

    try:
        proc = subprocess.Popen(cmd, **kwargs)
        stdout, stderr = proc.communicate(input=stdin_content, timeout=timeout)

        return CommandResult(
            returncode=proc.returncode,
            stdout=stdout or "",
            stderr=stderr or "",
            command=cmd,
        )
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise
    except FileNotFoundError:
        return CommandResult(
            returncode=127,
            stdout="",
            stderr=f"Command not found: {cmd[0]}",
            command=cmd,
        )


def run_command_with_tempfile(
    cmd: list[str],
    content: str,
    cwd: Optional[Union[str, Path]] = None,
    timeout: Optional[float] = None,
    env: Optional[dict[str, str]] = None,
    suffix: str = ".txt",
) -> CommandResult:
    """
    使用临时文件传递内容执行命令（heredoc 的安全替代方案）。

    适用于不支持 stdin 输入的命令。

    Args:
        cmd: 命令列表，其中 "{tempfile}" 将被替换为临时文件路径
        content: 要写入临时文件的内容
        cwd: 工作目录
        timeout: 超时时间（秒）
        env: 环境变量
        suffix: 临时文件后缀

    Returns:
        CommandResult 对象
    """
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=suffix,
        delete=False,
        encoding="utf-8",
    ) as f:
        f.write(content)
        temp_path = f.name

    try:
        # 替换命令中的 {tempfile} 占位符
        actual_cmd = [
            arg.replace("{tempfile}", temp_path) if "{tempfile}" in arg else arg
            for arg in cmd
        ]

        return run_command_with_stdin(
            actual_cmd,
            stdin_content=None,
            cwd=cwd,
            timeout=timeout,
            env=env,
        )
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


def build_codeagent_command(
    backend: str = "codex",
    use_stdin: bool = True,
    extra_args: Optional[list[str]] = None,
) -> SafeCommandBuilder:
    """
    构建 codeagent-wrapper 命令。

    Args:
        backend: 后端类型（如 codex）
        use_stdin: 是否从 stdin 读取任务
        extra_args: 额外参数

    Returns:
        SafeCommandBuilder 对象
    """
    builder = SafeCommandBuilder("codeagent-wrapper")
    builder.add_option("--backend", backend)

    if extra_args:
        for arg in extra_args:
            builder.add_arg(arg)

    if use_stdin:
        builder.add_flag("-")

    return builder


def build_gh_command(
    subcommand: str,
    *args: str,
    repo: Optional[str] = None,
    json_output: bool = False,
    jq_query: Optional[str] = None,
) -> SafeCommandBuilder:
    """
    构建 gh CLI 命令。

    Args:
        subcommand: 子命令（如 issue, pr, api）
        *args: 子命令参数
        repo: 仓库（如 owner/repo）
        json_output: 是否输出 JSON
        jq_query: jq 查询表达式

    Returns:
        SafeCommandBuilder 对象
    """
    builder = SafeCommandBuilder("gh")
    builder.add_arg(subcommand)

    for arg in args:
        builder.add_arg(arg)

    if repo:
        builder.add_option("--repo", repo)

    if json_output:
        builder.add_flag("--json")

    if jq_query:
        builder.add_option("-q", jq_query)

    return builder


def build_git_command(subcommand: str, *args: str) -> SafeCommandBuilder:
    """
    构建 git 命令。

    Args:
        subcommand: 子命令（如 commit, push, branch）
        *args: 子命令参数

    Returns:
        SafeCommandBuilder 对象
    """
    builder = SafeCommandBuilder("git")
    builder.add_arg(subcommand)

    for arg in args:
        builder.add_arg(arg)

    return builder


def build_python_script_command(
    script_path: Union[str, Path],
    *args: str,
    python_executable: str = "python3",
) -> SafeCommandBuilder:
    """
    构建 Python 脚本执行命令。

    Args:
        script_path: 脚本路径
        *args: 脚本参数
        python_executable: Python 解释器

    Returns:
        SafeCommandBuilder 对象
    """
    builder = SafeCommandBuilder(python_executable)
    builder.add_arg(str(script_path))

    for arg in args:
        builder.add_arg(arg)

    return builder


# 导出公共 API
__all__ = [
    "SafeCommandBuilder",
    "CommandResult",
    "quote_arg",
    "quote_args",
    "needs_escaping",
    "escape_for_logging",
    "run_command_with_stdin",
    "run_command_with_tempfile",
    "build_codeagent_command",
    "build_gh_command",
    "build_git_command",
    "build_python_script_command",
    "SHELL_SPECIAL_CHARS",
]
