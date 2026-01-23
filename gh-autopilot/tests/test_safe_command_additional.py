#!/usr/bin/env python3
"""
safe_command.py 补充分支覆盖测试。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# 添加 scripts 目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from safe_command import run_command_with_stdin, run_command_with_tempfile  # noqa: E402


def test_run_command_with_stdin_accepts_path_cwd():
    with tempfile.TemporaryDirectory() as td:
        result = run_command_with_stdin(
            ["python3", "-c", "print('ok')"],
            stdin_content=None,
            cwd=Path(td),
            timeout=5,
        )
        assert result.returncode == 0


def test_run_command_with_tempfile_handles_unlink_oserror():
    cmd = [
        "python3",
        "-c",
        "import pathlib,sys; print(pathlib.Path(sys.argv[1]).read_text())",
        "{tempfile}",
    ]

    with patch("os.unlink", side_effect=OSError("nope")):
        result = run_command_with_tempfile(cmd, content="hello", timeout=5)
    assert result.returncode == 0

