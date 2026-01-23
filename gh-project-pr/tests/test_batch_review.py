import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _import_batch_review():
    module_name = "scripts/batch_review"
    if module_name in sys.modules:
        return sys.modules[module_name]

    module_path = SCRIPTS_DIR / "batch_review.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec and spec.loader

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += float(seconds)


def _install_fake_gates(
    monkeypatch: pytest.MonkeyPatch,
    *,
    codex_approved: bool = True,
    ci_result: str = "success",
):
    def fake_review_pr_with_codex(pr_number: int, **kwargs):  # noqa: ANN001
        verdict = {
            "approved": bool(codex_approved),
            "blocking": [] if codex_approved else ["needs fix"],
            "summary": "ok" if codex_approved else "not ok",
            "confidence": 0.9,
        }
        return verdict

    def fake_wait_for_ci_success(repo: str, sha: str, **kwargs):  # noqa: ANN001
        assert repo
        assert sha
        return ci_result

    monkeypatch.setitem(
        sys.modules,
        "scripts/codex_review",
        types.SimpleNamespace(review_pr_with_codex=fake_review_pr_with_codex),
    )
    monkeypatch.setitem(
        sys.modules,
        "scripts/ci_gate",
        types.SimpleNamespace(get_ci_state=fake_wait_for_ci_success),
    )


def test_run_gh_success(monkeypatch: pytest.MonkeyPatch):
    batch_review = _import_batch_review()

    def fake_run(cmd, capture_output, text, timeout):  # noqa: ANN001
        assert capture_output is True
        assert text is True
        assert timeout == 3
        return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(batch_review.subprocess, "run", fake_run)
    assert batch_review._run_gh(["gh", "x"], timeout=3) == (0, "ok", "")


def test_run_gh_timeout(monkeypatch: pytest.MonkeyPatch):
    batch_review = _import_batch_review()

    def fake_run(cmd, capture_output, text, timeout):  # noqa: ANN001
        raise batch_review.subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    monkeypatch.setattr(batch_review.subprocess, "run", fake_run)
    returncode, stdout, stderr = batch_review._run_gh(["gh", "x"], timeout=1)
    assert returncode == -1
    assert stdout == ""
    assert "timed out" in stderr


def test_run_gh_generic_error(monkeypatch: pytest.MonkeyPatch):
    batch_review = _import_batch_review()

    def fake_run(cmd, capture_output, text, timeout):  # noqa: ANN001
        raise RuntimeError("boom")

    monkeypatch.setattr(batch_review.subprocess, "run", fake_run)
    returncode, stdout, stderr = batch_review._run_gh(["gh", "x"], timeout=1)
    assert returncode == -1
    assert stdout == ""
    assert stderr == "boom"


def test_run_gh_json_parses_and_handles_errors(monkeypatch: pytest.MonkeyPatch):
    batch_review = _import_batch_review()

    monkeypatch.setattr(batch_review, "_run_gh", lambda *a, **k: (0, '{"a": 1}', ""))
    assert batch_review._run_gh_json(["gh"], timeout=1) == {"a": 1}

    monkeypatch.setattr(batch_review, "_run_gh", lambda *a, **k: (1, "{}", "boom"))
    assert batch_review._run_gh_json(["gh"], timeout=1) is None

    monkeypatch.setattr(batch_review, "_run_gh", lambda *a, **k: (0, "{bad", ""))
    assert batch_review._run_gh_json(["gh"], timeout=1) is None


def test_load_sibling_module_returns_cached(monkeypatch: pytest.MonkeyPatch):
    batch_review = _import_batch_review()
    dummy = types.SimpleNamespace(x=1)
    monkeypatch.setitem(sys.modules, "scripts/_dummy", dummy)
    assert batch_review._load_sibling_module("scripts/_dummy", "does_not_matter.py") is dummy


def test_load_sibling_module_loads_from_file(tmp_path: Path):
    batch_review = _import_batch_review()

    dummy_file = tmp_path / "dummy_mod.py"
    dummy_file.write_text("value = 42\n", encoding="utf-8")

    original_file = batch_review.__file__
    try:
        # Pretend batch_review lives next to dummy_mod.py
        batch_review.__file__ = str(tmp_path / "batch_review.py")
        mod = batch_review._load_sibling_module("scripts/dummy_mod", "dummy_mod.py")
        assert getattr(mod, "value") == 42
    finally:
        batch_review.__file__ = original_file
        sys.modules.pop("scripts/dummy_mod", None)


def test_check_pr_status_variants(monkeypatch: pytest.MonkeyPatch):
    batch_review = _import_batch_review()

    monkeypatch.setattr(batch_review, "_run_gh_json", lambda *a, **k: None)
    assert batch_review.check_pr_status(1)["state"] == "unknown"

    monkeypatch.setattr(
        batch_review,
        "_run_gh_json",
        lambda *a, **k: {
            "state": "MERGED",
            "mergeable": "MERGEABLE",
            "statusCheckRollup": [],
        },
    )
    assert batch_review.check_pr_status(1) == {"state": "merged", "mergeable": False, "ci_status": "pass"}

    monkeypatch.setattr(
        batch_review,
        "_run_gh_json",
        lambda *a, **k: {
            "state": "CLOSED",
            "mergeable": "MERGEABLE",
            "statusCheckRollup": [],
        },
    )
    assert batch_review.check_pr_status(1) == {"state": "closed", "mergeable": False, "ci_status": "unknown"}

    monkeypatch.setattr(
        batch_review,
        "_run_gh_json",
        lambda *a, **k: {
            "state": "OPEN",
            "mergeable": "MERGEABLE",
            "statusCheckRollup": [{"status": "IN_PROGRESS", "conclusion": None}],
        },
    )
    assert batch_review.check_pr_status(1) == {"state": "open", "mergeable": True, "ci_status": "pending"}

    monkeypatch.setattr(
        batch_review,
        "_run_gh_json",
        lambda *a, **k: {
            "state": "OPEN",
            "mergeable": "MERGEABLE",
            "statusCheckRollup": [{"status": "COMPLETED", "conclusion": "FAILURE"}],
        },
    )
    assert batch_review.check_pr_status(1) == {"state": "open", "mergeable": True, "ci_status": "fail"}


def test_get_pr_metadata_variants(monkeypatch: pytest.MonkeyPatch):
    batch_review = _import_batch_review()

    monkeypatch.setattr(batch_review, "_run_gh_json", lambda *a, **k: None)
    assert batch_review.get_pr_metadata(1)["state"] == "unknown"

    monkeypatch.setattr(
        batch_review,
        "_run_gh_json",
        lambda *a, **k: {
            "state": "OPEN",
            "mergeable": "MERGEABLE",
            "headRefOid": "abc",
            "headRefName": "feat/x",
            "headRepository": {"nameWithOwner": "o/r"},
        },
    )
    assert batch_review.get_pr_metadata(1) == {
        "state": "open",
        "mergeable": True,
        "head_repo": "o/r",
        "head_sha": "abc",
        "head_ref": "feat/x",
    }

    monkeypatch.setattr(
        batch_review,
        "_run_gh_json",
        lambda *a, **k: {
            "state": "MERGED",
            "mergeable": "CONFLICTING",
            "headRefOid": None,
            "headRefName": None,
            "headRepository": {},
        },
    )
    assert batch_review.get_pr_metadata(1)["state"] == "merged"


def test_wait_for_ci_pass_fail_timeout(monkeypatch: pytest.MonkeyPatch):
    batch_review = _import_batch_review()
    clock = FakeClock()
    monkeypatch.setattr(batch_review.time, "time", clock.time)
    monkeypatch.setattr(batch_review.time, "sleep", clock.sleep)

    calls = {"n": 0}

    def fake_check(_pr: int):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 1:
            return {"ci_status": "pending"}
        return {"ci_status": "pass"}

    monkeypatch.setattr(batch_review, "check_pr_status", fake_check)
    assert batch_review.wait_for_ci(1, timeout_s=10, interval_s=5) == "pass"
    assert clock.now == 5.0

    monkeypatch.setattr(batch_review, "check_pr_status", lambda _pr: {"ci_status": "fail"})
    assert batch_review.wait_for_ci(1, timeout_s=10, interval_s=5) == "fail"

    monkeypatch.setattr(batch_review.time, "time", clock.time)
    monkeypatch.setattr(batch_review.time, "sleep", clock.sleep)
    monkeypatch.setattr(batch_review, "check_pr_status", lambda _pr: {"ci_status": "pending"})
    clock.now = 0.0
    assert batch_review.wait_for_ci(1, timeout_s=10, interval_s=3) == "timeout"
    assert clock.now >= 10.0


def test_approve_pr_success_and_failure(monkeypatch: pytest.MonkeyPatch):
    batch_review = _import_batch_review()

    monkeypatch.setattr(batch_review, "_run_gh", lambda *a, **k: (0, "", ""))
    assert batch_review.approve_pr(1) == (True, "")

    monkeypatch.setattr(batch_review, "_run_gh", lambda *a, **k: (1, "", "boom"))
    assert batch_review.approve_pr(1) == (False, "boom")


def test_merge_pr_includes_yes_and_squash(monkeypatch: pytest.MonkeyPatch):
    batch_review = _import_batch_review()

    captured: dict[str, list[str]] = {}

    def fake_run(cmd: list[str], timeout: int = 300):  # noqa: ANN001
        captured["cmd"] = cmd
        return 0, "", ""

    monkeypatch.setattr(batch_review, "_run_gh", fake_run)
    ok, err = batch_review.merge_pr(123, squash=True)
    assert ok is True
    assert err == ""
    assert captured["cmd"][:3] == ["gh", "pr", "merge"]
    assert "--yes" in captured["cmd"]
    assert "--squash" in captured["cmd"]
    assert "--delete-branch" not in captured["cmd"]

    ok, err = batch_review.merge_pr(123, squash=False)
    assert ok is True
    assert "--merge" in captured["cmd"]


def test_merge_pr_failure_returns_message(monkeypatch: pytest.MonkeyPatch):
    batch_review = _import_batch_review()
    monkeypatch.setattr(batch_review, "_run_gh", lambda *a, **k: (1, "", "nope"))
    ok, err = batch_review.merge_pr(1)
    assert ok is False
    assert err == "nope"


def test_delete_branch_success_and_missing(monkeypatch: pytest.MonkeyPatch):
    batch_review = _import_batch_review()

    assert batch_review.delete_branch("", "b") == (False, "missing repo or branch")
    assert batch_review.delete_branch("o/r", "") == (False, "missing repo or branch")

    captured: dict[str, list[str]] = {}

    def fake_run(cmd: list[str], timeout: int = 300):  # noqa: ANN001
        captured["cmd"] = cmd
        return 0, "", ""

    monkeypatch.setattr(batch_review, "_run_gh", fake_run)
    assert batch_review.delete_branch("o/r", "feat/x") == (True, "")
    assert captured["cmd"][:3] == ["gh", "api", "-X"]
    assert captured["cmd"][-1] == "repos/o/r/git/refs/heads/feat/x"


def test_review_single_pr_skips_merged_closed(monkeypatch: pytest.MonkeyPatch):
    batch_review = _import_batch_review()
    monkeypatch.setattr(batch_review.time, "sleep", lambda *_: None)

    monkeypatch.setattr(
        batch_review,
        "get_pr_metadata",
        lambda _pr: {
            "state": "merged",
            "mergeable": False,
            "head_repo": "o/r",
            "head_sha": "sha",
            "head_ref": "b",
        },
    )
    result = batch_review.review_single_pr(issue=1, pr=2)
    assert result.status == "skipped"

    monkeypatch.setattr(
        batch_review,
        "get_pr_metadata",
        lambda _pr: {
            "state": "closed",
            "mergeable": False,
            "head_repo": "o/r",
            "head_sha": "sha",
            "head_ref": "b",
        },
    )
    result = batch_review.review_single_pr(issue=1, pr=2)
    assert result.status == "skipped"


def test_review_single_pr_codex_not_approved_blocks_merge(monkeypatch: pytest.MonkeyPatch):
    batch_review = _import_batch_review()
    monkeypatch.setattr(batch_review.time, "sleep", lambda *_: None)
    _install_fake_gates(monkeypatch, codex_approved=False, ci_result="success")

    monkeypatch.setattr(
        batch_review,
        "get_pr_metadata",
        lambda _pr: {
            "state": "open",
            "mergeable": True,
            "head_repo": "o/r",
            "head_sha": "sha",
            "head_ref": "b",
        },
    )

    monkeypatch.setattr(batch_review, "approve_pr", lambda *_: (_ for _ in ()).throw(AssertionError("should not approve")))
    monkeypatch.setattr(batch_review, "merge_pr", lambda *_: (_ for _ in ()).throw(AssertionError("should not merge")))

    result = batch_review.review_single_pr(issue=1, pr=2, auto_merge=True)
    assert result.status == "failed"
    assert "codex not approved" in (result.error or "")


def test_review_single_pr_ci_failure_blocks_merge(monkeypatch: pytest.MonkeyPatch):
    batch_review = _import_batch_review()
    monkeypatch.setattr(batch_review.time, "sleep", lambda *_: None)
    _install_fake_gates(monkeypatch, codex_approved=True, ci_result="failure")

    monkeypatch.setattr(
        batch_review,
        "get_pr_metadata",
        lambda _pr: {
            "state": "open",
            "mergeable": True,
            "head_repo": "o/r",
            "head_sha": "sha",
            "head_ref": "b",
        },
    )
    monkeypatch.setattr(batch_review, "_run_gh", lambda *a, **k: (0, "sha\n", ""))

    monkeypatch.setattr(batch_review, "approve_pr", lambda *_: (_ for _ in ()).throw(AssertionError("should not approve")))
    monkeypatch.setattr(batch_review, "merge_pr", lambda *_: (_ for _ in ()).throw(AssertionError("should not merge")))

    result = batch_review.review_single_pr(issue=1, pr=2, auto_merge=True)
    assert result.status == "failed"
    assert result.error == "CI failed"


def test_review_single_pr_approved_without_merge(monkeypatch: pytest.MonkeyPatch):
    batch_review = _import_batch_review()
    monkeypatch.setattr(batch_review.time, "sleep", lambda *_: None)
    _install_fake_gates(monkeypatch, codex_approved=True, ci_result="success")

    monkeypatch.setattr(
        batch_review,
        "get_pr_metadata",
        lambda _pr: {
            "state": "open",
            "mergeable": True,
            "head_repo": "o/r",
            "head_sha": "sha",
            "head_ref": "b",
        },
    )
    monkeypatch.setattr(batch_review, "_run_gh", lambda *a, **k: (0, "sha\n", ""))

    monkeypatch.setattr(batch_review, "approve_pr", lambda *_: (True, ""))
    monkeypatch.setattr(batch_review, "merge_pr", lambda *_: (_ for _ in ()).throw(AssertionError("should not merge")))
    monkeypatch.setattr(batch_review, "delete_branch", lambda *_: (_ for _ in ()).throw(AssertionError("should not delete")))

    result = batch_review.review_single_pr(issue=1, pr=2, auto_merge=False)
    assert result.status == "approved"


def test_review_single_pr_merge_and_cleanup(monkeypatch: pytest.MonkeyPatch):
    batch_review = _import_batch_review()
    monkeypatch.setattr(batch_review.time, "sleep", lambda *_: None)
    _install_fake_gates(monkeypatch, codex_approved=True, ci_result="success")

    monkeypatch.setattr(
        batch_review,
        "get_pr_metadata",
        lambda _pr: {
            "state": "open",
            "mergeable": True,
            "head_repo": "o/r",
            "head_sha": "sha",
            "head_ref": "b",
        },
    )
    monkeypatch.setattr(batch_review, "_run_gh", lambda *a, **k: (0, "sha\n", ""))

    monkeypatch.setattr(batch_review, "approve_pr", lambda *_: (True, ""))
    monkeypatch.setattr(batch_review, "merge_pr", lambda *_: (True, ""))
    monkeypatch.setattr(batch_review, "delete_branch", lambda *_: (True, ""))

    result = batch_review.review_single_pr(issue=1, pr=2, auto_merge=True)
    assert result.status == "merged"
    assert result.error is None


def test_review_single_pr_merge_failure_does_not_delete(monkeypatch: pytest.MonkeyPatch):
    batch_review = _import_batch_review()
    monkeypatch.setattr(batch_review.time, "sleep", lambda *_: None)
    _install_fake_gates(monkeypatch, codex_approved=True, ci_result="success")

    monkeypatch.setattr(
        batch_review,
        "get_pr_metadata",
        lambda _pr: {
            "state": "open",
            "mergeable": True,
            "head_repo": "o/r",
            "head_sha": "sha",
            "head_ref": "b",
        },
    )
    monkeypatch.setattr(batch_review, "_run_gh", lambda *a, **k: (0, "sha\n", ""))

    monkeypatch.setattr(batch_review, "approve_pr", lambda *_: (True, ""))
    monkeypatch.setattr(batch_review, "merge_pr", lambda *_: (False, "boom"))
    monkeypatch.setattr(batch_review, "delete_branch", lambda *_: (_ for _ in ()).throw(AssertionError("should not delete")))

    result = batch_review.review_single_pr(issue=1, pr=2, auto_merge=True, max_retries=1)
    assert result.status == "failed"
    assert "merge failed" in (result.error or "")


def test_batch_review_serial_and_parallel_isolated(monkeypatch: pytest.MonkeyPatch):
    batch_review = _import_batch_review()

    def fake_review_single_pr(issue: int, pr: int, **kwargs):  # noqa: ANN001
        return batch_review.ReviewResult(issue=issue, pr=pr, status="approved", error=None, duration_s=0.1)

    monkeypatch.setattr(batch_review, "review_single_pr", fake_review_single_pr)
    results = batch_review.batch_review_serial(
        items=[{"issue": 1, "pr": 10}, {"issue": 2, "pr": None}],
        auto_merge=False,
        max_retries=1,
        verbose=True,
    )
    assert {r.status for r in results} == {"approved", "skipped"}

    def fake_review_single_pr_parallel(issue: int, pr: int, **kwargs):  # noqa: ANN001
        if pr == 11:
            raise RuntimeError("boom")
        return batch_review.ReviewResult(issue=issue, pr=pr, status="approved")

    monkeypatch.setattr(batch_review, "review_single_pr", fake_review_single_pr_parallel)
    results = batch_review.batch_review_parallel(
        items=[{"issue": 1, "pr": 10}, {"issue": 2, "pr": 11}],
        auto_merge=False,
        max_workers=2,
        verbose=True,
    )
    assert len(results) == 2
    assert any(r.status == "failed" for r in results)
    assert any(r.status == "approved" for r in results)

    # No valid PRs: returns only skipped results
    results = batch_review.batch_review_parallel(items=[{"issue": 3, "pr": None}])
    assert len(results) == 1
    assert results[0].status == "skipped"


def test_summarize_and_format_output():
    batch_review = _import_batch_review()
    results = [
        batch_review.ReviewResult(issue=1, pr=10, status="merged"),
        batch_review.ReviewResult(issue=2, pr=11, status="approved"),
        batch_review.ReviewResult(issue=3, pr=12, status="failed", error="x"),
        batch_review.ReviewResult(issue=4, pr=0, status="skipped", error="no PR linked"),
    ]
    summary = batch_review.summarize_results(results)
    assert summary.total == 4
    assert summary.merged == 1
    assert summary.approved == 1
    assert summary.failed == 1
    assert summary.skipped == 1

    output = batch_review.format_output(results, summary)
    assert output["summary"]["total"] == 4
    assert len(output["results"]) == 4


def test_main_handles_missing_and_bad_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    batch_review = _import_batch_review()

    monkeypatch.setattr(sys, "argv", ["batch_review.py", "--input", str(tmp_path / "missing.json")])
    with pytest.raises(SystemExit) as e:
        batch_review.main()
    assert e.value.code == 1

    bad_file = tmp_path / "bad.json"
    bad_file.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["batch_review.py", "--input", str(bad_file)])
    with pytest.raises(SystemExit) as e:
        batch_review.main()
    assert e.value.code == 1


def test_main_empty_input_exits_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    batch_review = _import_batch_review()

    f = tmp_path / "in.json"
    f.write_text(json.dumps({"sorted": []}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["batch_review.py", "--input", str(f)])
    with pytest.raises(SystemExit) as e:
        batch_review.main()
    assert e.value.code == 0

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["summary"]["total"] == 0


def test_main_runs_serial_and_parallel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    batch_review = _import_batch_review()

    f = tmp_path / "in.json"
    f.write_text(
        json.dumps({"sorted": [{"issue": 1, "pr": 10, "state": "open"}, {"issue": 2, "pr": 11, "state": "open"}]}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        batch_review,
        "batch_review_serial",
        lambda *a, **k: [
            batch_review.ReviewResult(issue=1, pr=10, status="merged"),
            batch_review.ReviewResult(issue=2, pr=11, status="merged"),
        ],
    )
    monkeypatch.setattr(sys, "argv", ["batch_review.py", "--input", str(f), "--verbose"])
    batch_review.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["merged"] == 2

    monkeypatch.setattr(
        batch_review,
        "batch_review_parallel",
        lambda *a, **k: [
            batch_review.ReviewResult(issue=1, pr=10, status="merged"),
            batch_review.ReviewResult(issue=2, pr=11, status="failed", error="x"),
        ],
    )
    monkeypatch.setattr(sys, "argv", ["batch_review.py", "--input", str(f), "--parallel"])
    with pytest.raises(SystemExit) as e:
        batch_review.main()
    assert e.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["failed"] == 1
