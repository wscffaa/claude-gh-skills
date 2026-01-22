#!/usr/bin/env python3
"""
命令格式解析单元测试。

测试 safe_command 模块的功能，验证：
- heredoc 格式命令的正确转义
- 特殊字符（引号、换行符、反斜杠、$、&、|、; 等）处理
- subprocess 调用参数的正确构造
- 模拟 zsh eval 场景验证

覆盖率目标: >= 90%
"""

import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

# 添加 scripts 目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from safe_command import (
    SafeCommandBuilder,
    CommandResult,
    quote_arg,
    quote_args,
    needs_escaping,
    escape_for_logging,
    run_command_with_stdin,
    run_command_with_tempfile,
    build_codeagent_command,
    build_gh_command,
    build_git_command,
    build_python_script_command,
    SHELL_SPECIAL_CHARS,
)


class TestSafeCommandBuilder:
    """SafeCommandBuilder 单元测试"""

    def test_basic_command(self):
        """测试基本命令构造"""
        builder = SafeCommandBuilder("git")
        cmd = builder.build()
        assert cmd == ["git"]

    def test_add_arg_single(self):
        """测试添加单个参数"""
        builder = SafeCommandBuilder("git")
        builder.add_arg("status")
        assert builder.build() == ["git", "status"]

    def test_add_arg_multiple(self):
        """测试添加多个参数"""
        builder = SafeCommandBuilder("git")
        builder.add_arg("commit", "-m", "fix bug")
        assert builder.build() == ["git", "commit", "-m", "fix bug"]

    def test_add_arg_none_ignored(self):
        """测试 None 参数被忽略"""
        builder = SafeCommandBuilder("git")
        builder.add_arg("status", None, "-v")
        assert builder.build() == ["git", "status", "-v"]

    def test_add_flag(self):
        """测试添加标志"""
        builder = SafeCommandBuilder("codeagent-wrapper")
        builder.add_flag("--backend")
        builder.add_flag("-")
        assert builder.build() == ["codeagent-wrapper", "--backend", "-"]

    def test_add_option(self):
        """测试添加选项"""
        builder = SafeCommandBuilder("codeagent-wrapper")
        builder.add_option("--backend", "codex")
        assert builder.build() == ["codeagent-wrapper", "--backend", "codex"]

    def test_add_option_none_value_ignored(self):
        """测试 None 值的选项被忽略"""
        builder = SafeCommandBuilder("git")
        builder.add_option("--repo", None)
        builder.add_option("--branch", "main")
        assert builder.build() == ["git", "--branch", "main"]

    def test_add_env(self):
        """测试添加环境变量"""
        builder = SafeCommandBuilder("python")
        builder.add_env("PYTHONPATH", "/custom/path")
        env = builder.build_env()
        assert env is not None
        assert env["PYTHONPATH"] == "/custom/path"

    def test_build_env_returns_none_when_empty(self):
        """测试无环境变量时返回 None"""
        builder = SafeCommandBuilder("python")
        assert builder.build_env() is None

    def test_chaining(self):
        """测试链式调用"""
        cmd = (
            SafeCommandBuilder("git")
            .add_arg("commit")
            .add_option("-m", "message")
            .add_flag("--verbose")
            .build()
        )
        assert cmd == ["git", "commit", "-m", "message", "--verbose"]

    def test_to_shell_string_basic(self):
        """测试转换为 shell 字符串"""
        builder = SafeCommandBuilder("echo")
        builder.add_arg("hello world")
        shell_str = builder.to_shell_string()
        assert "echo" in shell_str
        # shlex.quote 会为包含空格的参数添加引号
        assert "'hello world'" in shell_str or '"hello world"' in shell_str

    def test_to_shell_string_special_chars(self):
        """测试特殊字符在 shell 字符串中被正确转义"""
        builder = SafeCommandBuilder("echo")
        builder.add_arg("$HOME; rm -rf /")
        shell_str = builder.to_shell_string()
        # 确保特殊字符被转义
        assert "$" not in shell_str or "'" in shell_str  # 被引号包围

    def test_repr(self):
        """测试 __repr__"""
        builder = SafeCommandBuilder("git")
        builder.add_arg("status")
        repr_str = repr(builder)
        assert "SafeCommandBuilder" in repr_str
        assert "git" in repr_str


class TestQuoteFunctions:
    """引号转义函数测试"""

    def test_quote_arg_simple(self):
        """测试简单参数转义"""
        assert quote_arg("hello") == "hello"

    def test_quote_arg_with_space(self):
        """测试带空格参数转义"""
        result = quote_arg("hello world")
        # shlex.quote 返回 'hello world'
        assert "hello world" in result
        assert "'" in result or '"' in result

    def test_quote_arg_special_chars(self):
        """测试特殊字符转义"""
        result = quote_arg("$HOME")
        # 确保 $ 被转义
        assert "$HOME" in result
        # shlex.quote 会用单引号包围
        assert "'" in result

    def test_quote_arg_semicolon(self):
        """测试分号转义"""
        result = quote_arg("echo hello; rm -rf /")
        assert "'" in result  # 被引号包围

    def test_quote_args_multiple(self):
        """测试多参数转义"""
        results = quote_args("hello", "world with space", "$var")
        assert len(results) == 3
        assert results[0] == "hello"
        assert "'" in results[1]  # 空格需要引号
        assert "'" in results[2]  # $ 需要引号


class TestNeedsEscaping:
    """特殊字符检测测试"""

    def test_no_special_chars(self):
        """测试无特殊字符"""
        assert not needs_escaping("hello")
        assert not needs_escaping("hello123")
        assert not needs_escaping("hello-world")

    def test_space_needs_escaping(self):
        """测试空格需要转义"""
        assert needs_escaping("hello world")

    def test_dollar_needs_escaping(self):
        """测试 $ 需要转义"""
        assert needs_escaping("$HOME")
        assert needs_escaping("${VAR}")

    def test_semicolon_needs_escaping(self):
        """测试 ; 需要转义"""
        assert needs_escaping("cmd1; cmd2")

    def test_pipe_needs_escaping(self):
        """测试 | 需要转义"""
        assert needs_escaping("cmd1 | cmd2")

    def test_ampersand_needs_escaping(self):
        """测试 & 需要转义"""
        assert needs_escaping("cmd &")
        assert needs_escaping("cmd1 && cmd2")

    def test_quotes_need_escaping(self):
        """测试引号需要转义"""
        assert needs_escaping("'quoted'")
        assert needs_escaping('"double quoted"')

    def test_backslash_needs_escaping(self):
        """测试反斜杠需要转义"""
        assert needs_escaping("path\\to\\file")

    def test_newline_needs_escaping(self):
        """测试换行符需要转义"""
        assert needs_escaping("line1\nline2")

    def test_backtick_needs_escaping(self):
        """测试反引号需要转义"""
        assert needs_escaping("`whoami`")

    def test_redirect_needs_escaping(self):
        """测试重定向符需要转义"""
        assert needs_escaping("cmd > file")
        assert needs_escaping("cmd < file")


class TestEscapeForLogging:
    """日志转义函数测试"""

    def test_basic_string(self):
        """测试基本字符串"""
        assert escape_for_logging("hello") == "hello"

    def test_newline_escaped(self):
        """测试换行符转义"""
        result = escape_for_logging("line1\nline2")
        assert result == "line1\\nline2"

    def test_carriage_return_escaped(self):
        """测试回车符转义"""
        result = escape_for_logging("line1\rline2")
        assert result == "line1\\rline2"

    def test_tab_escaped(self):
        """测试制表符转义"""
        result = escape_for_logging("col1\tcol2")
        assert result == "col1\\tcol2"

    def test_truncation(self):
        """测试长字符串截断"""
        long_string = "a" * 300
        result = escape_for_logging(long_string, max_length=100)
        assert len(result) == 103  # 100 + "..."
        assert result.endswith("...")

    def test_no_truncation_when_short(self):
        """测试短字符串不截断"""
        short_string = "hello"
        result = escape_for_logging(short_string, max_length=100)
        assert result == "hello"


class TestRunCommandWithStdin:
    """run_command_with_stdin 函数测试"""

    def test_basic_echo(self):
        """测试基本命令执行"""
        result = run_command_with_stdin(["echo", "hello"])
        assert result.success
        assert result.returncode == 0
        assert "hello" in result.stdout

    def test_stdin_input(self):
        """测试 stdin 输入"""
        result = run_command_with_stdin(["cat"], stdin_content="test input")
        assert result.success
        assert "test input" in result.stdout

    def test_stdin_with_special_chars(self):
        """测试 stdin 包含特殊字符"""
        special_content = "hello $HOME; rm -rf / && echo 'test'"
        result = run_command_with_stdin(["cat"], stdin_content=special_content)
        assert result.success
        # 确保特殊字符原样传递，不被 shell 解释
        assert "$HOME" in result.stdout
        assert "rm -rf" in result.stdout

    def test_stdin_with_newlines(self):
        """测试 stdin 包含换行符"""
        multiline = "line1\nline2\nline3"
        result = run_command_with_stdin(["cat"], stdin_content=multiline)
        assert result.success
        assert "line1" in result.stdout
        assert "line2" in result.stdout

    def test_stdin_with_heredoc_like_content(self):
        """测试 stdin 包含类似 heredoc 的内容"""
        heredoc_like = """<<EOF
This is content that looks like heredoc
EOF"""
        result = run_command_with_stdin(["cat"], stdin_content=heredoc_like)
        assert result.success
        assert "<<EOF" in result.stdout
        assert "heredoc" in result.stdout

    def test_cwd_parameter(self):
        """测试工作目录参数"""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_command_with_stdin(["pwd"], cwd=tmpdir)
            assert result.success
            assert tmpdir in result.stdout or Path(tmpdir).resolve().as_posix() in result.stdout

    def test_command_not_found(self):
        """测试命令不存在"""
        result = run_command_with_stdin(["nonexistent_command_12345"])
        assert not result.success
        assert result.returncode == 127

    def test_command_failure(self):
        """测试命令失败"""
        result = run_command_with_stdin(["false"])
        assert not result.success
        assert result.returncode != 0

    def test_timeout(self):
        """测试超时"""
        with pytest.raises(subprocess.TimeoutExpired):
            run_command_with_stdin(["sleep", "10"], timeout=0.1)

    def test_command_result_bool(self):
        """测试 CommandResult 布尔值"""
        success_result = CommandResult(0, "out", "err", ["echo"])
        failure_result = CommandResult(1, "out", "err", ["false"])
        assert bool(success_result) is True
        assert bool(failure_result) is False


class TestRunCommandWithTempfile:
    """run_command_with_tempfile 函数测试"""

    def test_basic_usage(self):
        """测试基本用法"""
        result = run_command_with_tempfile(
            ["cat", "{tempfile}"],
            content="hello from tempfile",
        )
        assert result.success
        assert "hello from tempfile" in result.stdout

    def test_special_chars_in_content(self):
        """测试内容中的特殊字符"""
        special_content = "$VAR && echo 'test' | grep something"
        result = run_command_with_tempfile(
            ["cat", "{tempfile}"],
            content=special_content,
        )
        assert result.success
        assert "$VAR" in result.stdout

    def test_tempfile_cleanup(self):
        """测试临时文件被清理"""
        # 执行命令并获取临时文件路径（通过 cat 输出内容来验证）
        result = run_command_with_tempfile(
            ["cat", "{tempfile}"],
            content="test",
            suffix=".txt",
        )
        assert result.success
        # 临时文件应该已被删除，无法直接验证路径


class TestBuildCodeagentCommand:
    """build_codeagent_command 函数测试"""

    def test_default_params(self):
        """测试默认参数"""
        builder = build_codeagent_command()
        cmd = builder.build()
        assert cmd[0] == "codeagent-wrapper"
        assert "--backend" in cmd
        assert "codex" in cmd
        assert "-" in cmd

    def test_custom_backend(self):
        """测试自定义 backend"""
        builder = build_codeagent_command(backend="claude")
        cmd = builder.build()
        assert "claude" in cmd

    def test_no_stdin(self):
        """测试不使用 stdin"""
        builder = build_codeagent_command(use_stdin=False)
        cmd = builder.build()
        assert "-" not in cmd

    def test_extra_args(self):
        """测试额外参数"""
        builder = build_codeagent_command(extra_args=["--verbose", "--debug"])
        cmd = builder.build()
        assert "--verbose" in cmd
        assert "--debug" in cmd


class TestBuildGhCommand:
    """build_gh_command 函数测试"""

    def test_basic_command(self):
        """测试基本命令"""
        builder = build_gh_command("issue", "list")
        cmd = builder.build()
        assert cmd == ["gh", "issue", "list"]

    def test_with_repo(self):
        """测试带 repo 参数"""
        builder = build_gh_command("pr", "view", "123", repo="owner/repo")
        cmd = builder.build()
        assert "--repo" in cmd
        assert "owner/repo" in cmd

    def test_json_output(self):
        """测试 JSON 输出"""
        builder = build_gh_command("issue", "list", json_output=True)
        cmd = builder.build()
        assert "--json" in cmd

    def test_jq_query(self):
        """测试 jq 查询"""
        builder = build_gh_command("issue", "view", "123", jq_query=".title")
        cmd = builder.build()
        assert "-q" in cmd
        assert ".title" in cmd


class TestBuildGitCommand:
    """build_git_command 函数测试"""

    def test_basic_command(self):
        """测试基本命令"""
        builder = build_git_command("status")
        cmd = builder.build()
        assert cmd == ["git", "status"]

    def test_with_args(self):
        """测试带参数"""
        builder = build_git_command("commit", "-m", "fix: bug fix")
        cmd = builder.build()
        assert cmd == ["git", "commit", "-m", "fix: bug fix"]


class TestBuildPythonScriptCommand:
    """build_python_script_command 函数测试"""

    def test_basic_command(self):
        """测试基本命令"""
        builder = build_python_script_command("/path/to/script.py")
        cmd = builder.build()
        assert cmd == ["python3", "/path/to/script.py"]

    def test_with_args(self):
        """测试带参数"""
        builder = build_python_script_command("/path/to/script.py", "--verbose", "--input", "file.txt")
        cmd = builder.build()
        assert cmd == ["python3", "/path/to/script.py", "--verbose", "--input", "file.txt"]

    def test_custom_python(self):
        """测试自定义 Python 解释器"""
        builder = build_python_script_command("/path/to/script.py", python_executable="python3.11")
        cmd = builder.build()
        assert cmd[0] == "python3.11"

    def test_path_object(self):
        """测试 Path 对象"""
        builder = build_python_script_command(Path("/path/to/script.py"))
        cmd = builder.build()
        assert cmd == ["python3", "/path/to/script.py"]


class TestZshEvalScenarios:
    """模拟 zsh eval 场景测试"""

    def test_heredoc_content_via_stdin(self):
        """测试通过 stdin 传递 heredoc 内容（避免 shell 解析）"""
        # 这个内容如果通过 heredoc 传递会导致 zsh eval 错误
        dangerous_content = """
实现 Issue #42: 添加 $HOME 变量处理

Requirements:
- 处理 ${VAR} 变量
- 支持 `command` 替换
- 处理 'single' 和 "double" 引号
- 支持 && || ; 等操作符
"""
        result = run_command_with_stdin(["cat"], stdin_content=dangerous_content)
        assert result.success
        # 验证内容原样传递
        assert "$HOME" in result.stdout
        assert "${VAR}" in result.stdout
        assert "`command`" in result.stdout

    def test_multiline_command_content(self):
        """测试多行命令内容"""
        multiline = """Line 1 with $var
Line 2 with `command`
Line 3 with 'quotes' and "double"
Line 4 with special chars: & | ; < > ()
"""
        result = run_command_with_stdin(["cat"], stdin_content=multiline)
        assert result.success
        assert "$var" in result.stdout
        assert "`command`" in result.stdout

    def test_chinese_content(self):
        """测试中文内容"""
        chinese_content = "实现用户登录功能\n要求：支持多种认证方式"
        result = run_command_with_stdin(["cat"], stdin_content=chinese_content)
        assert result.success
        assert "实现用户登录功能" in result.stdout

    def test_mixed_content(self):
        """测试混合内容"""
        mixed = """
# 功能描述
实现 Issue #123: 添加新功能

## 技术要求
- 变量: $HOME, ${PATH}
- 命令: `ls -la`
- 引号: 'single' "double"
- 操作符: && || ; | < >

```bash
echo "hello"
ls -la | grep test
```
"""
        result = run_command_with_stdin(["cat"], stdin_content=mixed)
        assert result.success
        assert "Issue #123" in result.stdout
        assert "$HOME" in result.stdout


class TestSpecialCharacterHandling:
    """特殊字符处理测试"""

    @pytest.mark.parametrize("char,name", [
        ("$", "dollar sign"),
        ("`", "backtick"),
        ("&", "ampersand"),
        ("|", "pipe"),
        (";", "semicolon"),
        ("<", "less than"),
        (">", "greater than"),
        ("(", "open paren"),
        (")", "close paren"),
        ("'", "single quote"),
        ('"', "double quote"),
        ("\\", "backslash"),
        ("\n", "newline"),
        ("\t", "tab"),
        ("*", "asterisk"),
        ("?", "question mark"),
        ("[", "open bracket"),
        ("]", "close bracket"),
        ("#", "hash"),
        ("~", "tilde"),
        ("=", "equals"),
        ("%", "percent"),
        ("!", "exclamation"),
    ])
    def test_special_char_via_stdin(self, char, name):
        """测试各种特殊字符通过 stdin 传递"""
        content = f"test {char} content"
        result = run_command_with_stdin(["cat"], stdin_content=content)
        assert result.success, f"Failed for {name} ({repr(char)})"
        assert char in result.stdout or char == "\n", f"Char not preserved for {name}"

    def test_shell_injection_prevented(self):
        """测试 shell 注入被阻止"""
        # 这个内容如果被 shell 执行会很危险
        malicious = "hello; echo PWNED; rm -rf /"
        result = run_command_with_stdin(["cat"], stdin_content=malicious)
        assert result.success
        # 确保内容原样传递，不被执行
        assert "echo PWNED" in result.stdout
        assert "rm -rf" in result.stdout


class TestIntegrationWithBatchExecutor:
    """与 batch_executor 集成测试"""

    def test_build_task_content_escaping(self):
        """测试任务内容构建的转义"""
        # 模拟 batch_executor 中的任务内容构建
        issue_number = 42
        title = "Fix $HOME variable & add 'new' feature"

        # 确保特殊字符不会被处理
        safe_title = title.replace("\r", "").replace("\0", "")
        task_content = (
            f"实现 Issue #{issue_number}: {safe_title}\n\n"
            "Requirements:\n"
            "- 参考 issue 描述完成开发任务\n"
            f"- 创建 issue-{issue_number} 分支\n"
            "- 提交代码并创建 PR\n\n"
            "Deliverables:\n"
            "- 代码实现\n"
            "- 单元测试\n"
            f"- 创建 PR (分支名: issue-{issue_number})\n"
        )

        result = run_command_with_stdin(["cat"], stdin_content=task_content)
        assert result.success
        assert "$HOME" in result.stdout
        assert "'new'" in result.stdout
        assert "Issue #42" in result.stdout


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
