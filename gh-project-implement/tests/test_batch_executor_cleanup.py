from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import scripts.batch_executor as be


def _cp(cmd: list[str], returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)


def test_calculate_max_workers_respects_priority_and_dependencies() -> None:
    assert be._calculate_max_workers("p0", batch_size=10, has_dependencies=False) == 4
    assert be._calculate_max_workers("p1", batch_size=2, has_dependencies=False) == 2
    assert be._calculate_max_workers("p2", batch_size=1, has_dependencies=False) == 1
    assert be._calculate_max_workers("p3", batch_size=10, has_dependencies=True) == 1
    assert be._calculate_max_workers("unknown", batch_size=3, has_dependencies=True) == 1


def test_dag_scheduler_tracks_ready_completed_failed_and_blocked() -> None:
    specs = [
        be.IssueSpec(number=1, priority="p2", title="a", dependencies=[]),
        be.IssueSpec(number=2, priority="p2", title="b", dependencies=[1]),
        # dep=999 不在 specs 中，应视为无依赖
        be.IssueSpec(number=3, priority="p2", title="c", dependencies=[999]),
    ]
    scheduler = be.DagScheduler(specs)

    ready = scheduler.get_ready_issues()
    assert set(ready) == {1, 3}

    assert scheduler.mark_started(1) is True
    assert scheduler.mark_started(1) is False
    scheduler.mark_failed(1)

    # 依赖失败会阻塞 issue-2
    assert scheduler.has_blocked_issues() == [2]
    assert scheduler.get_ready_issues() == [3]

    assert scheduler.mark_started(3) is True
    scheduler.mark_completed(3)
    assert scheduler.is_done() is False


def test_extract_specs_supports_new_and_old_formats_and_dedupes(capsys: pytest.CaptureFixture[str]) -> None:
    data = {
        "batches": [
            {"priority": "P1", "issues": [{"number": 1, "title": "t1", "dependencies": [2, "x"]}]},
            {"priority": "p2", "issues": [2, "3", {"number": 2}]},  # 2 重复
            {"priority": "p3", "issues": ["not-a-number", 0, -1, {"number": "bad"}]},
        ]
    }
    specs, warnings = be._extract_specs(data)

    assert [s.number for s in specs] == [1, 2, 3]
    assert specs[0].priority == "p1"
    assert specs[0].dependencies == [2]
    assert any("重复 issue: #2" in w for w in warnings)
    assert capsys.readouterr() == ("", "")


def test_extract_specs_missing_batches_exits_with_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        be._extract_specs({"batches": "nope"})  # type: ignore[arg-type]
    assert exc.value.code == 1
    assert "缺少 batches 列表" in capsys.readouterr().err


def test_read_json_input_from_file(tmp_path: Path) -> None:
    p = tmp_path / "in.json"
    p.write_text('{"batches": []}', encoding="utf-8")
    assert be._read_json_input(str(p)) == {"batches": []}


def test_read_json_input_invalid_json_exits(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeStdin:
        def isatty(self) -> bool:
            return False

        def read(self) -> str:
            return "{not json"

    monkeypatch.setattr(be.sys, "stdin", _FakeStdin())
    with pytest.raises(SystemExit) as exc:
        be._read_json_input(None)
    assert exc.value.code == 1
    assert "JSON 解析失败" in capsys.readouterr().err


def test_read_json_input_top_level_not_object_exits(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeStdin:
        def isatty(self) -> bool:
            return False

        def read(self) -> str:
            return '["not object"]'

    monkeypatch.setattr(be.sys, "stdin", _FakeStdin())
    with pytest.raises(SystemExit) as exc:
        be._read_json_input(None)
    assert exc.value.code == 1
    assert "顶层必须为对象" in capsys.readouterr().err


def test_read_json_input_file_read_error_exits(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad_path = tmp_path / "missing.json"
    with patch.object(be.Path, "read_text", side_effect=OSError("boom")):
        with pytest.raises(SystemExit) as exc:
            be._read_json_input(str(bad_path))
    assert exc.value.code == 1
    assert "读取输入文件失败" in capsys.readouterr().err


def test_read_json_input_without_stdin_exits(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeStdin:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(be.sys, "stdin", _FakeStdin())
    with pytest.raises(SystemExit) as exc:
        be._read_json_input(None)
    assert exc.value.code == 1
    assert "未提供输入" in capsys.readouterr().err


def test_open_tty_stdin_handles_tty_and_missing_dev_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeStdin:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(be.sys, "stdin", _FakeStdin())
    assert be._open_tty_stdin() is None

    class _FakeStdin2:
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr(be.sys, "stdin", _FakeStdin2())

    def _raise(*_args, **_kwargs):
        raise OSError("no tty")

    monkeypatch.setattr("builtins.open", _raise)
    assert be._open_tty_stdin() is None


def test_last_nonempty_line_and_parse_session_id() -> None:
    assert be._last_nonempty_line("") == ""
    assert be._last_nonempty_line("a\n\n b \n") == "b"
    assert be._parse_session_id("no session") is None
    assert be._parse_session_id("") is None
    assert be._parse_session_id("SESSION_ID: abc\nSESSION_ID=def") == "def"


def test_format_duration_rounding_and_units() -> None:
    assert be._format_duration(0.1) == "0s"
    assert be._format_duration(0.6) == "1s"
    assert be._format_duration(61.2) == "1m1s"
    assert be._format_duration(3661) == "1h1m1s"


def test_stop_process_tries_terminate_then_kill() -> None:
    proc = type("P", (), {})()
    proc.poll = lambda: None
    proc.terminate = lambda: None
    proc.kill = lambda: None

    waits: list[float] = []

    def _wait(timeout: float):
        waits.append(timeout)
        if len(waits) < 3:
            raise subprocess.TimeoutExpired(cmd="x", timeout=timeout)
        return 0

    proc.wait = _wait
    be._stop_process(proc, timeout_sec=0.01)
    assert len(waits) >= 3


def test_stop_process_returns_early_when_already_exited() -> None:
    proc = type("P", (), {})()
    proc.poll = lambda: 0
    be._stop_process(proc)


def test_stop_process_returns_after_sigint_wait() -> None:
    proc = type("P", (), {})()
    proc.poll = lambda: None
    proc.send_signal = lambda _sig: None
    proc.wait = lambda timeout: 0
    be._stop_process(proc, timeout_sec=0.01)


def test_run_capture_success_and_filenotfound(monkeypatch: pytest.MonkeyPatch) -> None:
    state = be.ExecState()

    class _Proc:
        returncode = 0

        def communicate(self):
            return ("out", "err")

    def _popen(*_args, **_kwargs):
        return _Proc()

    monkeypatch.setattr(be.subprocess, "Popen", _popen)
    result = be._run_capture(["git", "status"], cwd=Path("."), state=state)
    assert result.returncode == 0
    assert result.stdout == "out"
    assert state.current_process is None
    assert state.last_process is not None

    def _popen_missing(*_args, **_kwargs):
        raise FileNotFoundError("missing")

    monkeypatch.setattr(be.subprocess, "Popen", _popen_missing)
    result2 = be._run_capture(["nope"], cwd=Path("."), state=state)
    assert result2.returncode == 127
    assert "missing" in (result2.stderr or "")


def test_create_worktree_parses_path_and_fallback_probe(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    worktree_script = tmp_path / "worktree.py"
    state = be.ExecState()

    calls: list[list[str]] = []

    def fake_run_capture(cmd: list[str], cwd: Path, state: be.ExecState) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[:3] == ["python3", str(worktree_script), "create"]:
            # stdout 无路径，触发 probe
            return _cp(cmd, 0, "\n", "")
        if cmd[:3] == ["python3", str(worktree_script), "path"]:
            return _cp(cmd, 0, "/tmp/wt\n", "")
        pytest.fail(f"unexpected cmd: {cmd}")

    with patch.object(be, "_run_capture", side_effect=fake_run_capture):
        path = be._create_worktree(worktree_script, issue_number=1, repo_dir=repo_dir, state=state)

    assert path == Path("/tmp/wt")
    assert calls[0][2] == "create"
    assert calls[1][2] == "path"


def test_create_worktree_failure_raises(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    worktree_script = tmp_path / "worktree.py"
    state = be.ExecState()

    def fake_run_capture(cmd: list[str], cwd: Path, state: be.ExecState) -> subprocess.CompletedProcess[str]:
        return _cp(cmd, 1, "", "boom")

    with patch.object(be, "_run_capture", side_effect=fake_run_capture):
        with pytest.raises(RuntimeError):
            be._create_worktree(worktree_script, issue_number=1, repo_dir=repo_dir, state=state)


def test_create_worktree_raises_when_path_unavailable(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    worktree_script = tmp_path / "worktree.py"
    state = be.ExecState()

    def fake_run_capture(cmd: list[str], cwd: Path, state: be.ExecState) -> subprocess.CompletedProcess[str]:
        if cmd[:3] == ["python3", str(worktree_script), "create"]:
            return _cp(cmd, 0, "\n", "")
        if cmd[:3] == ["python3", str(worktree_script), "path"]:
            return _cp(cmd, 0, "\n", "")
        pytest.fail(f"unexpected cmd: {cmd}")

    with patch.object(be, "_run_capture", side_effect=fake_run_capture):
        with pytest.raises(RuntimeError) as exc:
            be._create_worktree(worktree_script, issue_number=1, repo_dir=repo_dir, state=state)

    assert "无法解析 worktree 路径" in str(exc.value)


def test_get_worktree_path_returns_none_on_error(tmp_path: Path) -> None:
    state = be.ExecState()

    def fake_run_capture(cmd: list[str], cwd: Path, state: be.ExecState) -> subprocess.CompletedProcess[str]:
        return _cp(cmd, 1, "", "nope")

    with patch.object(be, "_run_capture", side_effect=fake_run_capture):
        assert be._get_worktree_path(tmp_path / "worktree.py", issue_number=1, repo_dir=tmp_path, state=state) is None


def test_force_remove_worktree_failure_includes_fallback_message(tmp_path: Path) -> None:
    state = be.ExecState()

    def fake_run_capture(cmd: list[str], cwd: Path, state: be.ExecState) -> subprocess.CompletedProcess[str]:
        return _cp(cmd, 1, "", "")

    with patch.object(be, "_run_capture", side_effect=fake_run_capture):
        ok, detail = be._force_remove_worktree(1, worktree_path=Path("/tmp/wt"), repo_dir=tmp_path, state=state)

    assert ok is False
    assert "git worktree remove --force 失败" in detail


def test_cleanup_all_resources_uses_active_worktrees_and_merges_force_errors(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    worktree_script = tmp_path / "worktree.py"

    state = be.ExecState()
    state.created_issues = {1}
    state.active_worktrees[1] = Path("/tmp/wt-1")

    def fake_run_capture(cmd: list[str], cwd: Path, state: be.ExecState) -> subprocess.CompletedProcess[str]:
        if cmd == ["python3", str(worktree_script), "remove", "1"]:
            return _cp(cmd, 1, "", "rm err")
        if cmd == ["python3", str(worktree_script), "path", "1"]:
            return _cp(cmd, 1, "", "no path")
        if cmd == ["git", "worktree", "remove", "--force", "/tmp/wt-1"]:
            return _cp(cmd, 1, "", "force err")
        if cmd == ["git", "branch", "-D", "issue-1"]:
            return _cp(cmd, 0, "", "")
        if cmd == ["git", "push", "origin", "--delete", "issue-1"]:
            return _cp(cmd, 0, "", "")
        if cmd == ["git", "worktree", "prune"]:
            return _cp(cmd, 0, "", "")
        pytest.fail(f"unexpected cmd: {cmd}")

    with patch.object(be, "_run_capture", side_effect=fake_run_capture):
        report = be._cleanup_all_resources(state=state, repo_dir=repo_dir, worktree_script=worktree_script)

    assert report.worktree_force_used == {1}
    ok, detail = report.worktree_removed[1]
    assert ok is False
    assert "rm err" in detail and "force err" in detail


def test_run_claude_records_session_id_and_handles_missing_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = be.ExecState()

    class _Proc:
        returncode = 0

        def communicate(self, input: str):
            return ("SESSION_ID: abc\n", "")

    monkeypatch.setattr(be.subprocess, "Popen", lambda *_a, **_k: _Proc())
    rc = be._run_claude(issue_number=1, title="t", worktree_path=tmp_path, state=state)
    assert rc == 0
    assert state.session_ids[1] == "abc"

    def _missing(*_a, **_k):
        raise FileNotFoundError()

    monkeypatch.setattr(be.subprocess, "Popen", _missing)
    assert be._run_claude(issue_number=2, title="t", worktree_path=tmp_path, state=state) == 127


def test_get_pr_number_parses_output_and_raises_on_error(tmp_path: Path) -> None:
    state = be.ExecState()

    def ok(cmd: list[str], cwd: Path, state: be.ExecState) -> subprocess.CompletedProcess[str]:
        return _cp(cmd, 0, "123\n", "")

    with patch.object(be, "_run_capture", side_effect=ok):
        assert be._get_pr_number(1, repo=None, cwd=tmp_path, state=state) == 123

    def null(cmd: list[str], cwd: Path, state: be.ExecState) -> subprocess.CompletedProcess[str]:
        return _cp(cmd, 0, "null\n", "")

    with patch.object(be, "_run_capture", side_effect=null):
        assert be._get_pr_number(1, repo=None, cwd=tmp_path, state=state) is None

    def bad(cmd: list[str], cwd: Path, state: be.ExecState) -> subprocess.CompletedProcess[str]:
        return _cp(cmd, 1, "", "err")

    with patch.object(be, "_run_capture", side_effect=bad):
        with pytest.raises(RuntimeError):
            be._get_pr_number(1, repo=None, cwd=tmp_path, state=state)


def test_get_pr_number_with_repo_and_non_digit_returns_none(tmp_path: Path) -> None:
    state = be.ExecState()

    def fake_run_capture(cmd: list[str], cwd: Path, state: be.ExecState) -> subprocess.CompletedProcess[str]:
        assert "--repo" in cmd
        return _cp(cmd, 0, "not-a-number\n", "")

    with patch.object(be, "_run_capture", side_effect=fake_run_capture):
        assert be._get_pr_number(1, repo="owner/repo", cwd=tmp_path, state=state) is None


def test_run_pr_review_success_and_missing_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = be.ExecState()

    class _Proc:
        returncode = 0

        def communicate(self, input: str):
            return ("ok", "")

    monkeypatch.setattr(be.subprocess, "Popen", lambda *_a, **_k: _Proc())
    assert be._run_pr_review(pr_number=1, worktree_path=tmp_path, tty_stdin=None, state=state) == 0

    def _missing(*_a, **_k):
        raise FileNotFoundError()

    monkeypatch.setattr(be.subprocess, "Popen", _missing)
    assert be._run_pr_review(pr_number=2, worktree_path=tmp_path, tty_stdin=None, state=state) == 127


def test_merge_pr_success_and_failure(tmp_path: Path) -> None:
    state = be.ExecState()

    def ok(cmd: list[str], cwd: Path, state: be.ExecState) -> subprocess.CompletedProcess[str]:
        return _cp(cmd, 0, "", "")

    with patch.object(be, "_run_capture", side_effect=ok):
        assert be._merge_pr(1, repo=None, cwd=tmp_path, state=state) == (True, "")

    def fail(cmd: list[str], cwd: Path, state: be.ExecState) -> subprocess.CompletedProcess[str]:
        return _cp(cmd, 1, "", "fail")

    with patch.object(be, "_run_capture", side_effect=fail):
        ok2, detail = be._merge_pr(1, repo=None, cwd=tmp_path, state=state)
    assert ok2 is False
    assert "fail" in detail


def test_merge_pr_includes_repo_argument(tmp_path: Path) -> None:
    state = be.ExecState()

    def fake_run_capture(cmd: list[str], cwd: Path, state: be.ExecState) -> subprocess.CompletedProcess[str]:
        assert "--repo" in cmd
        return _cp(cmd, 0, "", "")

    with patch.object(be, "_run_capture", side_effect=fake_run_capture):
        assert be._merge_pr(1, repo="owner/repo", cwd=tmp_path, state=state) == (True, "")


def test_print_report_and_cleanup_report_have_expected_sections(capsys: pytest.CaptureFixture[str]) -> None:
    results = [
        be.IssueResult(number=1, priority="p2", title="a" * 100, status="completed", pr_number=10, elapsed_sec=1.2, attempts=2),
        be.IssueResult(number=2, priority="p2", title="b", status="failed", pr_number=None, elapsed_sec=2.0, attempts=2),
        be.IssueResult(number=3, priority="p2", title="c", status="skipped", pr_number=None, elapsed_sec=0.1, attempts=1),
        be.IssueResult(number=4, priority="p2", title="d", status="interrupted", pr_number=None, elapsed_sec=0.1, attempts=1),
    ]
    be._print_report(results, interrupted=True)
    out = capsys.readouterr().out
    assert "完成报告" in out
    assert "issue" in out
    assert "PR" in out
    assert "已中断: 是" in out

    report = be.CleanupReport(
        tracked_issues=[1, 2],
        worktree_removed={1: (True, ""), 2: (False, "x")},
        worktree_force_used={2},
        local_branch_deleted={1: (True, ""), 2: (True, "")},
        remote_branch_deleted={1: (True, ""), 2: (True, "")},
        prune_ok=False,
        prune_detail="oops",
    )
    be._print_cleanup_report(report)
    out2 = capsys.readouterr().out
    assert "Cleanup Report" in out2
    assert "worktree --force" in out2
    assert "FAILED" in out2


def test_print_cleanup_report_empty_and_large_lists(capsys: pytest.CaptureFixture[str]) -> None:
    be._print_cleanup_report(be.CleanupReport(tracked_issues=[]))
    assert "无需清理" in capsys.readouterr().out

    issues = list(range(1, 22))
    report = be.CleanupReport(
        tracked_issues=issues,
        worktree_removed={i: (False, "x") for i in issues},
        worktree_force_used=set(issues),
        local_branch_deleted={i: (True, "") for i in issues},
        remote_branch_deleted={i: (True, "") for i in issues},
        prune_ok=True,
        prune_detail="",
    )
    be._print_cleanup_report(report)
    out = capsys.readouterr().out
    assert "worktree --force: 21" in out
    assert "... 还有" in out


def test_parse_issue_numbers_csv_invalid_values_raise() -> None:
    assert be._parse_issue_numbers_csv("1,2,2") == [1, 2]
    assert be._parse_issue_numbers_csv("1,,2") == [1, 2]
    with pytest.raises(ValueError):
        be._parse_issue_numbers_csv("1,a")
    with pytest.raises(ValueError):
        be._parse_issue_numbers_csv("0")


def test_is_issue_merged_via_gh_and_git_paths(tmp_path: Path) -> None:
    state = be.ExecState()

    def fake_run_capture(cmd: list[str], cwd: Path, state: be.ExecState) -> subprocess.CompletedProcess[str]:
        if cmd[:3] == ["gh", "pr", "list"]:
            return _cp(cmd, 0, "42\n", "")
        pytest.fail(f"unexpected cmd: {cmd}")

    with patch.object(be, "_run_capture", side_effect=fake_run_capture):
        merged, detail = be._is_issue_merged_via_gh(1, repo=None, repo_dir=tmp_path, state=state)
    assert merged is True
    assert detail == ""

    def gh_missing_then_git(cmd: list[str], cwd: Path, state: be.ExecState) -> subprocess.CompletedProcess[str]:
        if cmd[:3] == ["gh", "pr", "list"]:
            return _cp(cmd, 127, "", "gh missing")
        if cmd == ["git", "symbolic-ref", "refs/remotes/origin/HEAD"]:
            return _cp(cmd, 0, "refs/remotes/origin/main\n", "")
        if cmd[:3] == ["git", "branch", "--merged"]:
            return _cp(cmd, 0, "  issue-1\n", "")
        pytest.fail(f"unexpected cmd: {cmd}")

    with patch.object(be, "_run_capture", side_effect=gh_missing_then_git):
        merged2, detail2 = be._is_issue_merged(1, repo=None, repo_dir=tmp_path, state=state)
    assert merged2 is True
    assert "merged" in detail2


def test_cmd_cleanup_invalid_issues_csv_returns_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo_dir = tmp_path / "repo"
    worktree_script = tmp_path / "worktree.py"
    args = SimpleNamespace(cleanup_force=False, cleanup_issues="1,a", repo="owner/repo")
    rc = be.cmd_cleanup(args=args, repo_dir=repo_dir, worktree_script=worktree_script)
    assert rc == 2
    assert "--cleanup-issues 解析失败" in capsys.readouterr().err


def test_cmd_cleanup_no_candidates_returns_0(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo_dir = tmp_path / "repo"
    worktree_script = tmp_path / "worktree.py"
    args = SimpleNamespace(cleanup_force=False, cleanup_issues=None, repo="owner/repo")

    def fake_run_capture(cmd: list[str], cwd: Path, state: be.ExecState) -> subprocess.CompletedProcess[str]:
        # candidates 为空
        if cmd in (
            ["git", "branch", "--list", "issue-*"],
            ["git", "branch", "-r", "--list", "origin/issue-*"],
            ["git", "worktree", "list", "--porcelain"],
        ):
            return _cp(cmd, 0, "", "")
        pytest.fail(f"unexpected cmd: {cmd}")

    with patch.object(be, "_run_capture", side_effect=fake_run_capture):
        rc = be.cmd_cleanup(args=args, repo_dir=repo_dir, worktree_script=worktree_script)

    assert rc == 0
    assert "- 无需清理" in capsys.readouterr().out


def test_cmd_cleanup_to_clean_empty_returns_0(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo_dir = tmp_path / "repo"
    worktree_script = tmp_path / "worktree.py"
    args = SimpleNamespace(cleanup_force=False, cleanup_issues=None, repo="owner/repo")

    def fake_run_capture(cmd: list[str], cwd: Path, state: be.ExecState) -> subprocess.CompletedProcess[str]:
        if cmd == ["git", "branch", "--list", "issue-*"]:
            return _cp(cmd, 0, "  issue-1\n", "")
        if cmd == ["git", "branch", "-r", "--list", "origin/issue-*"]:
            return _cp(cmd, 0, "", "")
        if cmd == ["git", "worktree", "list", "--porcelain"]:
            return _cp(cmd, 0, "", "")

        if cmd[:3] == ["gh", "pr", "list"]:
            return _cp(cmd, 0, "null\n", "")

        pytest.fail(f"unexpected cmd: {cmd}")

    with patch.object(be, "_run_capture", side_effect=fake_run_capture):
        rc = be.cmd_cleanup(args=args, repo_dir=repo_dir, worktree_script=worktree_script)

    assert rc == 0
    out = capsys.readouterr().out
    assert "- 将清理: 0" in out
    assert "- 无需清理" in out


def test_cmd_cleanup_failures_return_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo_dir = tmp_path / "repo"
    worktree_script = tmp_path / "worktree.py"
    args = SimpleNamespace(cleanup_force=True, cleanup_issues=None, repo="owner/repo")

    def fake_run_capture(cmd: list[str], cwd: Path, state: be.ExecState) -> subprocess.CompletedProcess[str]:
        if cmd == ["git", "branch", "--list", "issue-*"]:
            return _cp(cmd, 0, "  issue-1\n", "")
        if cmd == ["git", "branch", "-r", "--list", "origin/issue-*"]:
            return _cp(cmd, 0, "", "")
        if cmd == ["git", "worktree", "list", "--porcelain"]:
            return _cp(cmd, 0, "", "")
        pytest.fail(f"unexpected cmd: {cmd}")

    report = be.CleanupReport(
        tracked_issues=[1],
        worktree_removed={1: (False, "x")},
        local_branch_deleted={1: (True, "")},
        remote_branch_deleted={1: (True, "")},
        prune_ok=True,
        prune_detail="",
    )

    with (
        patch.object(be, "_run_capture", side_effect=fake_run_capture),
        patch.object(be, "_cleanup_all_resources", return_value=report),
    ):
        rc = be.cmd_cleanup(args=args, repo_dir=repo_dir, worktree_script=worktree_script)

    assert rc == 1
    assert "⚠️ 清理完成" in capsys.readouterr().out


def test_execute_single_issue_completed_without_pr(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    state = be.ExecState()
    spec = be.IssueSpec(number=1, priority="p2", title="t", dependencies=[])

    with (
        patch.object(be, "_create_worktree", return_value=tmp_path / "wt"),
        patch.object(be, "_run_claude", return_value=0),
        patch.object(be, "_get_pr_number", return_value=None),
        patch.object(be, "_remove_worktree", return_value=(True, "")),
        patch.object(be.time, "monotonic", side_effect=[0.0, 1.0]),
    ):
        result = be._execute_single_issue(
            spec=spec,
            idx=1,
            total=1,
            prio_label="P2",
            repo=None,
            repo_dir=tmp_path,
            worktree_script=tmp_path / "worktree.py",
            max_retries=0,
            force_cleanup=False,
            tty_stdin=None,
            state=state,
            print_lock=be.Lock(),
        )

    assert result.status == "completed"
    assert result.pr_number is None
    assert result.attempts == 1
    assert 1 in state.created_issues
    assert 1 not in state.active_issues
    assert "✅ Issue #1 已完成" in capsys.readouterr().out


def test_execute_single_issue_completed_with_pr_merge(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    state = be.ExecState()
    spec = be.IssueSpec(number=2, priority="p2", title="t", dependencies=[])

    with (
        patch.object(be, "_create_worktree", return_value=tmp_path / "wt"),
        patch.object(be, "_run_claude", return_value=0),
        patch.object(be, "_get_pr_number", return_value=123),
        patch.object(be, "_run_pr_review", return_value=0),
        patch.object(be, "_merge_pr", return_value=(True, "")),
        patch.object(be, "_remove_worktree", return_value=(True, "")),
        patch.object(be.time, "monotonic", side_effect=[0.0, 1.0]),
    ):
        result = be._execute_single_issue(
            spec=spec,
            idx=1,
            total=1,
            prio_label="P2",
            repo=None,
            repo_dir=tmp_path,
            worktree_script=tmp_path / "worktree.py",
            max_retries=0,
            force_cleanup=False,
            tty_stdin=None,
            state=state,
            print_lock=be.Lock(),
        )

    assert result.status == "completed"
    assert result.pr_number == 123
    assert "PR #123 已合并" in capsys.readouterr().out


def test_execute_single_issue_retries_then_succeeds(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    state = be.ExecState()
    spec = be.IssueSpec(number=3, priority="p2", title="t", dependencies=[])

    run_claude = patch.object(be, "_run_claude", side_effect=[1, 0])
    get_worktree_path = patch.object(be, "_get_worktree_path", return_value=tmp_path / "oldwt")
    remove_worktree = patch.object(be, "_remove_worktree", return_value=(False, "rm failed"))
    force_remove = patch.object(be, "_force_remove_worktree", return_value=(True, ""))
    cleanup_remote = patch.object(be, "_cleanup_remote_branch", return_value=(False, "rb failed"))

    with (
        patch.object(be, "_create_worktree", side_effect=[tmp_path / "wt1", tmp_path / "wt2"]),
        run_claude,
        patch.object(be, "_get_pr_number", return_value=None),
        get_worktree_path,
        remove_worktree,
        force_remove,
        cleanup_remote,
        patch.object(be.time, "monotonic", side_effect=[0.0, 0.1, 1.0]),
    ):
        result = be._execute_single_issue(
            spec=spec,
            idx=1,
            total=1,
            prio_label="P2",
            repo=None,
            repo_dir=tmp_path,
            worktree_script=tmp_path / "worktree.py",
            max_retries=1,
            force_cleanup=True,
            tty_stdin=None,
            state=state,
            print_lock=be.Lock(),
        )

    assert result.status == "completed"
    assert result.attempts == 2
    out = capsys.readouterr().out
    assert "第 1/1 次重试" in out
    assert "✅ Issue #3 已完成" in out


def test_execute_single_issue_fails_when_merge_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    state = be.ExecState()
    spec = be.IssueSpec(number=4, priority="p2", title="t", dependencies=[])

    with (
        patch.object(be, "_create_worktree", return_value=tmp_path / "wt"),
        patch.object(be, "_run_claude", return_value=0),
        patch.object(be, "_get_pr_number", return_value=123),
        patch.object(be, "_run_pr_review", return_value=0),
        patch.object(be, "_merge_pr", return_value=(False, "merge failed")),
        patch.object(be, "_remove_worktree", return_value=(True, "")),
        patch.object(be.time, "monotonic", side_effect=[0.0, 1.0]),
    ):
        result = be._execute_single_issue(
            spec=spec,
            idx=1,
            total=1,
            prio_label="P2",
            repo=None,
            repo_dir=tmp_path,
            worktree_script=tmp_path / "worktree.py",
            max_retries=0,
            force_cleanup=False,
            tty_stdin=None,
            state=state,
            print_lock=be.Lock(),
        )

    assert result.status == "failed"
    assert "❌ Issue #4 失败" in capsys.readouterr().out


def test_execute_single_issue_keyboardinterrupt_marks_interrupted(tmp_path: Path) -> None:
    state = be.ExecState()
    spec = be.IssueSpec(number=5, priority="p2", title="t", dependencies=[])

    with (
        patch.object(be, "_create_worktree", side_effect=KeyboardInterrupt),
        patch.object(be.time, "monotonic", side_effect=[0.0, 1.0]),
    ):
        result = be._execute_single_issue(
            spec=spec,
            idx=1,
            total=1,
            prio_label="P2",
            repo=None,
            repo_dir=tmp_path,
            worktree_script=tmp_path / "worktree.py",
            max_retries=0,
            force_cleanup=False,
            tty_stdin=None,
            state=state,
            print_lock=be.Lock(),
        )

    assert result.status == "interrupted"
    assert state.interrupted is True


def test_execute_batch_concurrent_success_and_blocked(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    state = be.ExecState()
    results: list[be.IssueResult] = []
    results_lock = be.Lock()

    specs_ok = [
        be.IssueSpec(number=1, priority="p1", title="a", dependencies=[]),
        be.IssueSpec(number=2, priority="p1", title="b", dependencies=[]),
    ]

    def exec_ok(*_a, **_k):
        spec = _k["spec"]
        return be.IssueResult(number=spec.number, priority=spec.priority, title=spec.title, status="completed")

    with patch.object(be, "_execute_single_issue", side_effect=exec_ok):
        completed = be._execute_batch_concurrent(
            batch_specs=specs_ok,
            batch_priority="p1",
            start_idx=1,
            total=2,
            repo=None,
            repo_dir=tmp_path,
            worktree_script=tmp_path / "worktree.py",
            max_retries=0,
            force_cleanup=False,
            tty_stdin=None,
            state=state,
            results=results,
            results_lock=results_lock,
        )

    assert completed == 2
    assert {r.number for r in results} == {1, 2}
    assert "批次完成" in capsys.readouterr().out

    # blocked scenario: issue-2 depends on failed issue-1
    state2 = be.ExecState()
    results2: list[be.IssueResult] = []

    specs_blocked = [
        be.IssueSpec(number=1, priority="p1", title="a", dependencies=[]),
        be.IssueSpec(number=2, priority="p1", title="b", dependencies=[1]),
    ]

    def exec_mixed(*_a, **_k):
        spec = _k["spec"]
        status = "failed" if spec.number == 1 else "completed"
        return be.IssueResult(number=spec.number, priority=spec.priority, title=spec.title, status=status)

    with patch.object(be, "_execute_single_issue", side_effect=exec_mixed):
        completed2 = be._execute_batch_concurrent(
            batch_specs=specs_blocked,
            batch_priority="p1",
            start_idx=1,
            total=2,
            repo=None,
            repo_dir=tmp_path,
            worktree_script=tmp_path / "worktree.py",
            max_retries=0,
            force_cleanup=False,
            tty_stdin=None,
            state=state2,
            results=results2,
            results_lock=be.Lock(),
        )

    assert completed2 == 0
    assert any(r.status == "skipped" and r.number == 2 for r in results2)
    assert "因依赖失败而跳过" in capsys.readouterr().out


def test_main_exits_on_missing_worktree_script(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = SimpleNamespace(
        input=None,
        repo=None,
        repo_dir=str(tmp_path),
        worktree_script=str(tmp_path / "missing.py"),
        force_cleanup=False,
        cleanup=False,
        cleanup_force=False,
        cleanup_issues=None,
        max_retries=0,
        max_workers=0,
    )

    with patch.object(be.argparse.ArgumentParser, "parse_args", return_value=args):
        with pytest.raises(SystemExit) as exc:
            be.main()

    assert exc.value.code == 1
    assert "worktree.py 不存在" in capsys.readouterr().err


def test_main_cleanup_flag_delegates_to_cmd_cleanup(tmp_path: Path) -> None:
    args = SimpleNamespace(
        input=None,
        repo=None,
        repo_dir=str(tmp_path),
        worktree_script=str(be.DEFAULT_WORKTREE_SCRIPT),
        force_cleanup=False,
        cleanup=True,
        cleanup_force=False,
        cleanup_issues=None,
        max_retries=0,
        max_workers=0,
    )

    with (
        patch.object(be.argparse.ArgumentParser, "parse_args", return_value=args),
        patch.object(be, "cmd_cleanup", return_value=7),
        pytest.raises(SystemExit) as exc,
    ):
        be.main()
    assert exc.value.code == 7


def test_main_negative_max_retries_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = SimpleNamespace(
        input=None,
        repo=None,
        repo_dir=str(tmp_path),
        worktree_script=str(be.DEFAULT_WORKTREE_SCRIPT),
        force_cleanup=False,
        cleanup=False,
        cleanup_force=False,
        cleanup_issues=None,
        max_retries=-1,
        max_workers=0,
    )

    with patch.object(be.argparse.ArgumentParser, "parse_args", return_value=args):
        with pytest.raises(SystemExit) as exc:
            be.main()

    assert exc.value.code == 2
    assert "--max-retries 必须" in capsys.readouterr().err


def test_main_empty_specs_prints_report_and_returns(tmp_path: Path) -> None:
    args = SimpleNamespace(
        input=None,
        repo=None,
        repo_dir=str(tmp_path),
        worktree_script=str(be.DEFAULT_WORKTREE_SCRIPT),
        force_cleanup=False,
        cleanup=False,
        cleanup_force=False,
        cleanup_issues=None,
        max_retries=0,
        max_workers=0,
    )

    with (
        patch.object(be.argparse.ArgumentParser, "parse_args", return_value=args),
        patch.object(be, "_read_json_input", return_value={"batches": []}),
        patch.object(be, "_extract_specs", return_value=([], [])),
        patch.object(be, "_print_report") as pr,
    ):
        be.main()
    pr.assert_called_once()


def test_main_keyboardinterrupt_triggers_force_cleanup_and_exit_130(tmp_path: Path) -> None:
    args = SimpleNamespace(
        input=None,
        repo=None,
        repo_dir=str(tmp_path),
        worktree_script=str(be.DEFAULT_WORKTREE_SCRIPT),
        force_cleanup=False,
        cleanup=False,
        cleanup_force=False,
        cleanup_issues=None,
        max_retries=0,
        max_workers=0,
    )

    def raise_interrupt(**kwargs):
        # 模拟执行中记录活跃 worktree，然后中断
        kwargs["state"].active_worktrees[1] = tmp_path / "wt"
        raise KeyboardInterrupt

    with (
        patch.object(be.argparse.ArgumentParser, "parse_args", return_value=args),
        patch.object(be, "_read_json_input", return_value={"batches": [{"priority": "p2", "issues": [1]}]}),
        patch.object(be, "_extract_specs", return_value=([be.IssueSpec(number=1, priority="p2", title="t")], [])),
        patch.object(be, "_execute_batch_concurrent", side_effect=raise_interrupt),
        patch.object(be, "_force_remove_worktree", return_value=(True, "")) as fr,
        patch.object(be, "_cleanup_all_resources", return_value=be.CleanupReport()),
        patch.object(be, "_print_report"),
        patch.object(be, "_print_cleanup_report"),
        patch.object(be.signal, "signal"),
        pytest.raises(SystemExit) as exc,
    ):
        be.main()

    assert exc.value.code == 130
    fr.assert_called_once()


def test_main_exits_1_when_any_failed_result(tmp_path: Path) -> None:
    args = SimpleNamespace(
        input=None,
        repo=None,
        repo_dir=str(tmp_path),
        worktree_script=str(be.DEFAULT_WORKTREE_SCRIPT),
        force_cleanup=False,
        cleanup=False,
        cleanup_force=False,
        cleanup_issues=None,
        max_retries=0,
        max_workers=0,
    )

    def append_failed(**kwargs):
        kwargs["results"].append(be.IssueResult(number=1, priority="p2", title="t", status="failed"))
        return 0

    with (
        patch.object(be.argparse.ArgumentParser, "parse_args", return_value=args),
        patch.object(be, "_read_json_input", return_value={"batches": [{"priority": "p2", "issues": [1]}]}),
        patch.object(be, "_extract_specs", return_value=([be.IssueSpec(number=1, priority="p2", title="t")], [])),
        patch.object(be, "_execute_batch_concurrent", side_effect=append_failed),
        patch.object(be, "_cleanup_all_resources", return_value=be.CleanupReport()),
        patch.object(be, "_print_report"),
        patch.object(be, "_print_cleanup_report"),
        patch.object(be.signal, "signal"),
        pytest.raises(SystemExit) as exc,
    ):
        be.main()

    assert exc.value.code == 1

def test_cleanup_all_resources_normal_order_and_success(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    worktree_script = tmp_path / "worktree.py"

    state = be.ExecState()
    state.created_issues = {2, 1}

    calls: list[list[str]] = []

    def fake_run_capture(cmd: list[str], cwd: Path, state: be.ExecState) -> subprocess.CompletedProcess[str]:
        assert cwd == repo_dir
        calls.append(cmd)

        if cmd == ["git", "worktree", "prune"]:
            return _cp(cmd, 0, "", "")
        if cmd[:3] == ["git", "branch", "-D"]:
            return _cp(cmd, 0, "", "")
        if cmd[:4] == ["git", "push", "origin", "--delete"]:
            return _cp(cmd, 0, "", "")
        if cmd[:3] == ["python3", str(worktree_script), "remove"]:
            return _cp(cmd, 0, "", "")

        pytest.fail(f"unexpected cmd: {cmd}")

    with patch.object(be, "_run_capture", side_effect=fake_run_capture):
        report = be._cleanup_all_resources(state=state, repo_dir=repo_dir, worktree_script=worktree_script)

    assert report.tracked_issues == [1, 2]
    assert report.worktree_force_used == set()
    assert report.worktree_removed == {1: (True, ""), 2: (True, "")}
    assert report.local_branch_deleted == {1: (True, ""), 2: (True, "")}
    assert report.remote_branch_deleted == {1: (True, ""), 2: (True, "")}
    assert report.prune_ok is True

    assert calls == [
        ["python3", str(worktree_script), "remove", "1"],
        ["git", "branch", "-D", "issue-1"],
        ["git", "push", "origin", "--delete", "issue-1"],
        ["python3", str(worktree_script), "remove", "2"],
        ["git", "branch", "-D", "issue-2"],
        ["git", "push", "origin", "--delete", "issue-2"],
        ["git", "worktree", "prune"],
    ]


def test_cleanup_all_resources_empty_created_issues_still_prunes(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    worktree_script = tmp_path / "worktree.py"

    state = be.ExecState()
    state.created_issues = set()

    calls: list[list[str]] = []

    def fake_run_capture(cmd: list[str], cwd: Path, state: be.ExecState) -> subprocess.CompletedProcess[str]:
        assert cwd == repo_dir
        calls.append(cmd)
        if cmd == ["git", "worktree", "prune"]:
            return _cp(cmd, 0, "", "")
        pytest.fail(f"unexpected cmd: {cmd}")

    with patch.object(be, "_run_capture", side_effect=fake_run_capture):
        report = be._cleanup_all_resources(state=state, repo_dir=repo_dir, worktree_script=worktree_script)

    assert report.tracked_issues == []
    assert report.worktree_removed == {}
    assert report.local_branch_deleted == {}
    assert report.remote_branch_deleted == {}
    assert report.prune_ok is True
    assert calls == [["git", "worktree", "prune"]]


def test_cleanup_all_resources_partial_failures_force_and_tolerant_errors(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    worktree_script = tmp_path / "worktree.py"

    state = be.ExecState()
    state.created_issues = {1, 2}

    calls: list[list[str]] = []

    def fake_run_capture(cmd: list[str], cwd: Path, state: be.ExecState) -> subprocess.CompletedProcess[str]:
        assert cwd == repo_dir
        calls.append(cmd)

        # Issue 1: remove 失败 -> 走 path + force remove
        if cmd == ["python3", str(worktree_script), "remove", "1"]:
            return _cp(cmd, 1, "", "remove failed")
        if cmd == ["python3", str(worktree_script), "path", "1"]:
            return _cp(cmd, 0, "/tmp/wt-1\n", "")
        if cmd == ["git", "worktree", "remove", "--force", "/tmp/wt-1"]:
            return _cp(cmd, 0, "", "")

        # Issue 2: remove 失败但提示 not found -> 视为成功，不触发 force
        if cmd == ["python3", str(worktree_script), "remove", "2"]:
            return _cp(cmd, 1, "", "Worktree not found")

        # Local branch cleanup
        if cmd == ["git", "branch", "-D", "issue-1"]:
            return _cp(cmd, 1, "", "cannot delete branch")
        if cmd == ["git", "branch", "-D", "issue-2"]:
            return _cp(cmd, 1, "", "error: branch 'issue-2' not found.")

        # Remote branch cleanup
        if cmd == ["git", "push", "origin", "--delete", "issue-1"]:
            return _cp(cmd, 1, "", "permission denied")
        if cmd == ["git", "push", "origin", "--delete", "issue-2"]:
            return _cp(cmd, 1, "", "remote ref does not exist")

        # Prune 失败
        if cmd == ["git", "worktree", "prune"]:
            return _cp(cmd, 1, "", "prune failed")

        pytest.fail(f"unexpected cmd: {cmd}")

    with patch.object(be, "_run_capture", side_effect=fake_run_capture):
        report = be._cleanup_all_resources(state=state, repo_dir=repo_dir, worktree_script=worktree_script)

    assert report.tracked_issues == [1, 2]
    assert report.worktree_force_used == {1}
    assert report.worktree_removed[1] == (True, "")
    assert report.worktree_removed[2] == (True, "")

    assert report.local_branch_deleted[1][0] is False
    assert report.local_branch_deleted[2] == (True, "")

    assert report.remote_branch_deleted[1][0] is False
    assert report.remote_branch_deleted[2] == (True, "")

    assert report.prune_ok is False
    assert report.prune_detail == "prune failed"

    assert calls == [
        ["python3", str(worktree_script), "remove", "1"],
        ["python3", str(worktree_script), "path", "1"],
        ["git", "worktree", "remove", "--force", "/tmp/wt-1"],
        ["git", "branch", "-D", "issue-1"],
        ["git", "push", "origin", "--delete", "issue-1"],
        ["python3", str(worktree_script), "remove", "2"],
        ["git", "branch", "-D", "issue-2"],
        ["git", "push", "origin", "--delete", "issue-2"],
        ["git", "worktree", "prune"],
    ]


def test_cmd_cleanup_default_mode_only_cleans_merged(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo_dir = tmp_path / "repo"
    worktree_script = tmp_path / "worktree.py"
    args = SimpleNamespace(cleanup_force=False, cleanup_issues=None, repo="owner/repo")

    def fake_run_capture(cmd: list[str], cwd: Path, state: be.ExecState) -> subprocess.CompletedProcess[str]:
        assert cwd == repo_dir

        # candidates: {1,2,3}
        if cmd == ["git", "branch", "--list", "issue-*"]:
            return _cp(cmd, 0, "  issue-1\n  issue-2\n", "")
        if cmd == ["git", "branch", "-r", "--list", "origin/issue-*"]:
            return _cp(cmd, 0, "  origin/issue-3\n", "")
        if cmd == ["git", "worktree", "list", "--porcelain"]:
            return _cp(cmd, 0, "branch refs/heads/issue-2\n", "")

        # merged check
        if cmd[:3] == ["gh", "pr", "list"]:
            head = cmd[cmd.index("--head") + 1]
            if head == "issue-1":
                return _cp(cmd, 0, "123\n", "")
            if head == "issue-2":
                return _cp(cmd, 0, "null\n", "")
            if head == "issue-3":
                return _cp(cmd, 127, "", "gh not found")

        # fallback for issue-3: git merged check 失败 => unknown
        if cmd == ["git", "symbolic-ref", "refs/remotes/origin/HEAD"]:
            return _cp(cmd, 0, "refs/remotes/origin/main\n", "")
        if cmd == ["git", "branch", "--merged", "origin/main", "--list", "issue-3"]:
            return _cp(cmd, 1, "", "fatal: something went wrong")

        pytest.fail(f"unexpected cmd: {cmd}")

    expected_report = be.CleanupReport(
        tracked_issues=[1],
        worktree_removed={1: (True, "")},
        local_branch_deleted={1: (True, "")},
        remote_branch_deleted={1: (True, "")},
        prune_ok=True,
        prune_detail="",
    )

    def fake_cleanup_all_resources(state: be.ExecState, repo_dir: Path, worktree_script: Path) -> be.CleanupReport:
        assert state.created_issues == {1}
        return expected_report

    with (
        patch.object(be, "_run_capture", side_effect=fake_run_capture),
        patch.object(be, "_cleanup_all_resources", side_effect=fake_cleanup_all_resources),
    ):
        rc = be.cmd_cleanup(args=args, repo_dir=repo_dir, worktree_script=worktree_script)

    assert rc == 0

    out = capsys.readouterr().out
    assert "🧹 手动清理: --cleanup" in out
    assert "- 模式: merged-only" in out
    assert "- 将清理: 1" in out


def test_cmd_cleanup_force_mode_cleans_all_candidates(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo_dir = tmp_path / "repo"
    worktree_script = tmp_path / "worktree.py"
    args = SimpleNamespace(cleanup_force=True, cleanup_issues=None, repo="owner/repo")

    def fake_run_capture(cmd: list[str], cwd: Path, state: be.ExecState) -> subprocess.CompletedProcess[str]:
        assert cwd == repo_dir

        if cmd == ["git", "branch", "--list", "issue-*"]:
            return _cp(cmd, 0, "  issue-1\n  issue-2\n", "")
        if cmd == ["git", "branch", "-r", "--list", "origin/issue-*"]:
            return _cp(cmd, 0, "  origin/issue-3\n", "")
        if cmd == ["git", "worktree", "list", "--porcelain"]:
            return _cp(cmd, 0, "", "")

        pytest.fail(f"unexpected cmd: {cmd}")

    expected_report = be.CleanupReport(
        tracked_issues=[1, 2, 3],
        worktree_removed={1: (True, ""), 2: (True, ""), 3: (True, "")},
        local_branch_deleted={1: (True, ""), 2: (True, ""), 3: (True, "")},
        remote_branch_deleted={1: (True, ""), 2: (True, ""), 3: (True, "")},
        prune_ok=True,
        prune_detail="",
    )

    def fake_cleanup_all_resources(state: be.ExecState, repo_dir: Path, worktree_script: Path) -> be.CleanupReport:
        assert state.created_issues == {1, 2, 3}
        return expected_report

    with (
        patch.object(be, "_run_capture", side_effect=fake_run_capture),
        patch.object(be, "_cleanup_all_resources", side_effect=fake_cleanup_all_resources),
    ):
        rc = be.cmd_cleanup(args=args, repo_dir=repo_dir, worktree_script=worktree_script)

    assert rc == 0

    out = capsys.readouterr().out
    assert "- 模式: force" in out
    assert "- 将清理: 3" in out


def test_cmd_cleanup_issues_mode_parses_and_dedupes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo_dir = tmp_path / "repo"
    worktree_script = tmp_path / "worktree.py"
    args = SimpleNamespace(cleanup_force=False, cleanup_issues="10,11,10", repo="owner/repo")

    def fake_run_capture(cmd: list[str], cwd: Path, state: be.ExecState) -> subprocess.CompletedProcess[str]:
        assert cwd == repo_dir

        if cmd[:3] == ["gh", "pr", "list"]:
            head = cmd[cmd.index("--head") + 1]
            if head == "issue-10":
                return _cp(cmd, 0, "999\n", "")
            if head == "issue-11":
                return _cp(cmd, 0, "null\n", "")

        pytest.fail(f"unexpected cmd: {cmd}")

    expected_report = be.CleanupReport(
        tracked_issues=[10],
        worktree_removed={10: (True, "")},
        local_branch_deleted={10: (True, "")},
        remote_branch_deleted={10: (True, "")},
        prune_ok=True,
        prune_detail="",
    )

    def fake_cleanup_all_resources(state: be.ExecState, repo_dir: Path, worktree_script: Path) -> be.CleanupReport:
        assert state.created_issues == {10}
        return expected_report

    with (
        patch.object(be, "_run_capture", side_effect=fake_run_capture),
        patch.object(be, "_cleanup_all_resources", side_effect=fake_cleanup_all_resources),
    ):
        rc = be.cmd_cleanup(args=args, repo_dir=repo_dir, worktree_script=worktree_script)

    assert rc == 0

    out = capsys.readouterr().out
    assert "- 模式: merged-only (指定 issues)" in out
    assert "- 候选 issues: 2" in out
    assert "- 将清理: 1" in out


def test_run_gh_issue_title_uses_subprocess_run_and_strips_output(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    with patch.object(be.subprocess, "run") as run:
        run.return_value = subprocess.CompletedProcess(["gh"], 0, "Some Title\n", "")
        title = be._run_gh_issue_title(issue_number=123, repo="owner/repo", cwd=cwd)

    assert title == "Some Title"
    run.assert_called_once()
