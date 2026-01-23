import importlib.util
import sys
import types
from pathlib import Path

import pytest

# Import scripts/ci_gate.py under the exact module name used by `--cov=scripts/ci_gate`
# so that pytest-cov can collect coverage data for this target.
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"


def _import_ci_gate():
    module_name = "scripts/ci_gate"
    if module_name in sys.modules:
        return sys.modules[module_name]

    module_path = SCRIPTS_DIR / "ci_gate.py"
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


def test_gh_api_json_parses_json(monkeypatch):
    ci_gate = _import_ci_gate()

    def fake_run(cmd, capture_output, text, timeout, check):  # noqa: ANN001
        assert cmd[0:2] == ["gh", "api"]
        assert cmd[-1] == "repos/o/r/commits/sha/status"
        return types.SimpleNamespace(returncode=0, stdout='{"ok": true}', stderr="")

    monkeypatch.setattr(ci_gate.subprocess, "run", fake_run)
    payload = ci_gate._gh_api_json("repos/o/r/commits/sha/status", timeout_s=3)
    assert payload == {"ok": True}


def test_gh_api_json_merges_headers(monkeypatch):
    ci_gate = _import_ci_gate()

    def fake_run(cmd, capture_output, text, timeout, check):  # noqa: ANN001
        # Custom header should override default Accept
        assert "Accept: text/plain" in cmd
        return types.SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(ci_gate.subprocess, "run", fake_run)
    assert ci_gate._gh_api_json("repos/o/r/commits/sha/status", headers={"Accept": "text/plain"}) == {}


def test_gh_api_json_raises_on_nonzero_exit(monkeypatch):
    ci_gate = _import_ci_gate()

    def fake_run(cmd, capture_output, text, timeout, check):  # noqa: ANN001
        return types.SimpleNamespace(returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(ci_gate.subprocess, "run", fake_run)
    with pytest.raises(ci_gate.GhApiError):
        ci_gate._gh_api_json("repos/o/r/commits/sha/status", timeout_s=3)


def test_gh_api_json_raises_on_bad_json(monkeypatch):
    ci_gate = _import_ci_gate()

    def fake_run(cmd, capture_output, text, timeout, check):  # noqa: ANN001
        return types.SimpleNamespace(returncode=0, stdout="{not json", stderr="")

    monkeypatch.setattr(ci_gate.subprocess, "run", fake_run)
    with pytest.raises(ci_gate.GhApiError):
        ci_gate._gh_api_json("repos/o/r/commits/sha/status", timeout_s=3)


def test_run_cmd_translates_common_errors(monkeypatch):
    ci_gate = _import_ci_gate()

    def raise_timeout(*args, **kwargs):  # noqa: ANN001
        raise ci_gate.subprocess.TimeoutExpired(cmd=["gh"], timeout=1)

    monkeypatch.setattr(ci_gate.subprocess, "run", raise_timeout)
    with pytest.raises(ci_gate.GhApiError):
        ci_gate._run_cmd(["gh", "api", "x"], timeout_s=1)

    def raise_not_found(*args, **kwargs):  # noqa: ANN001
        raise FileNotFoundError("gh")

    monkeypatch.setattr(ci_gate.subprocess, "run", raise_not_found)
    with pytest.raises(ci_gate.GhApiError):
        ci_gate._run_cmd(["gh", "api", "x"], timeout_s=1)

    def raise_generic(*args, **kwargs):  # noqa: ANN001
        raise RuntimeError("boom")

    monkeypatch.setattr(ci_gate.subprocess, "run", raise_generic)
    with pytest.raises(ci_gate.GhApiError):
        ci_gate._run_cmd(["gh", "api", "x"], timeout_s=1)


def test_helpers_handle_field_compatibility(monkeypatch):
    ci_gate = _import_ci_gate()

    monkeypatch.setenv("CI_GATE_TIMEOUT_S", "not-an-int")
    assert ci_gate._parse_int_env("CI_GATE_TIMEOUT_S", 7) == 7

    monkeypatch.setenv("CI_GATE_FAIL_FAST", "true")
    assert ci_gate._parse_bool_env("CI_GATE_FAIL_FAST", False) is True
    monkeypatch.setenv("CI_GATE_FAIL_FAST", "0")
    assert ci_gate._parse_bool_env("CI_GATE_FAIL_FAST", True) is False
    monkeypatch.setenv("CI_GATE_FAIL_FAST", "maybe")
    assert ci_gate._parse_bool_env("CI_GATE_FAIL_FAST", True) is True

    assert ci_gate._normalize_commit_state("error") == "failure"
    assert ci_gate._normalize_commit_state("???") is None

    assert ci_gate._normalize_check_item({"conclusion": "cancelled"}) == "failure"
    assert ci_gate._normalize_check_item({"conclusion": "weird"}) == "pending"
    assert ci_gate._normalize_check_item({"state": "success"}) == "success"
    assert ci_gate._normalize_check_item({"state": "queued"}) == "pending"
    assert ci_gate._normalize_check_item({"status": "completed"}) == "pending"
    assert ci_gate._normalize_check_item({"status": "unknown"}) == "pending"

    assert ci_gate._summarize_commit_status([]) == ("pending", 0)
    assert ci_gate._summarize_commit_status({"statuses": ["bad", {"state": "success"}]}) == ("success", 1)

    assert ci_gate._summarize_check_runs([]) == ("pending", 0)
    assert ci_gate._summarize_check_runs({"check_runs": "bad"}) == ("pending", 0)
    assert ci_gate._summarize_check_runs({"check_runs": ["bad"]}) == ("success", 0)


def test_wait_for_ci_success_reads_env_defaults(monkeypatch):
    ci_gate = _import_ci_gate()

    monkeypatch.setenv("CI_GATE_TIMEOUT_S", "600")
    monkeypatch.setenv("CI_GATE_INTERVAL_S", "0")
    monkeypatch.setenv("CI_GATE_API_TIMEOUT_S", "30")
    monkeypatch.setenv("CI_GATE_FAIL_FAST", "1")
    monkeypatch.setattr(ci_gate, "get_ci_state", lambda *args, **kwargs: "success")

    assert (
        ci_gate.wait_for_ci_success(
            "o/r",
            "sha",
            time_fn=lambda: 0.0,
            sleep_fn=lambda _: None,
        )
        == "success"
    )


def test_wait_for_ci_success_all_success_returns_success(monkeypatch):
    ci_gate = _import_ci_gate()

    def fake_gh_api_json(endpoint, *, timeout_s=30, headers=None):  # noqa: ANN001
        if endpoint.endswith("/status"):
            return {"state": "success", "statuses": [{"state": "success"}]}
        if endpoint.endswith("/check-runs"):
            return {"total_count": 1, "check_runs": [{"status": "completed", "conclusion": "success"}]}
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    monkeypatch.setattr(ci_gate, "_gh_api_json", fake_gh_api_json)
    clock = FakeClock()
    assert (
        ci_gate.wait_for_ci_success(
            "o/r",
            "sha",
            timeout_s=10,
            interval_s=0,
            time_fn=clock.time,
            sleep_fn=clock.sleep,
        )
        == "success"
    )


def test_wait_for_ci_success_failure_fast_exits_without_sleep(monkeypatch):
    ci_gate = _import_ci_gate()

    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    def fake_gh_api_json(endpoint, *, timeout_s=30, headers=None):  # noqa: ANN001
        if endpoint.endswith("/status"):
            return {"state": "success", "statuses": [{"state": "success"}]}
        if endpoint.endswith("/check-runs"):
            # cover "state" compatibility as well
            return {"total_count": 1, "check_runs": [{"state": "failure"}]}
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    monkeypatch.setattr(ci_gate, "_gh_api_json", fake_gh_api_json)
    clock = FakeClock()
    result = ci_gate.wait_for_ci_success(
        "o/r",
        "sha",
        timeout_s=100,
        interval_s=30,
        time_fn=clock.time,
        sleep_fn=fake_sleep,
    )
    assert result == "failure"
    assert sleep_calls == []


def test_wait_for_ci_success_polls_until_success(monkeypatch):
    ci_gate = _import_ci_gate()
    clock = FakeClock()

    def fake_gh_api_json(endpoint, *, timeout_s=30, headers=None):  # noqa: ANN001
        first_round = clock.now == 0
        if endpoint.endswith("/status"):
            return {
                "state": "pending" if first_round else "success",
                "statuses": [{"state": "pending" if first_round else "success"}],
            }
        if endpoint.endswith("/check-runs"):
            return {
                "total_count": 1,
                "check_runs": [
                    {"status": "in_progress" if first_round else "completed", "conclusion": None if first_round else "success"}
                ],
            }
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    monkeypatch.setattr(ci_gate, "_gh_api_json", fake_gh_api_json)
    result = ci_gate.wait_for_ci_success(
        "o/r",
        "sha",
        timeout_s=20,
        interval_s=5,
        time_fn=clock.time,
        sleep_fn=clock.sleep,
    )
    assert result == "success"
    assert clock.now == 5.0


def test_wait_for_ci_success_times_out(monkeypatch):
    ci_gate = _import_ci_gate()
    clock = FakeClock()

    def fake_gh_api_json(endpoint, *, timeout_s=30, headers=None):  # noqa: ANN001
        if endpoint.endswith("/status"):
            return {"state": "pending", "statuses": [{"state": "pending"}]}
        if endpoint.endswith("/check-runs"):
            return {"total_count": 1, "check_runs": [{"status": "queued", "conclusion": None}]}
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    monkeypatch.setattr(ci_gate, "_gh_api_json", fake_gh_api_json)
    result = ci_gate.wait_for_ci_success(
        "o/r",
        "sha",
        timeout_s=10,
        interval_s=3,
        time_fn=clock.time,
        sleep_fn=clock.sleep,
    )
    assert result == "timeout"
    assert clock.now >= 10


def test_empty_checks_returns_success(monkeypatch):
    ci_gate = _import_ci_gate()

    def fake_gh_api_json(endpoint, *, timeout_s=30, headers=None):  # noqa: ANN001
        if endpoint.endswith("/status"):
            return {"state": "success", "statuses": []}
        if endpoint.endswith("/check-runs"):
            return {"total_count": 0, "check_runs": []}
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    monkeypatch.setattr(ci_gate, "_gh_api_json", fake_gh_api_json)
    clock = FakeClock()
    assert (
        ci_gate.wait_for_ci_success(
            "o/r",
            "sha",
            timeout_s=1,
            interval_s=0,
            time_fn=clock.time,
            sleep_fn=clock.sleep,
        )
        == "success"
    )


def test_api_exception_returns_failure_and_logs(monkeypatch, caplog):
    ci_gate = _import_ci_gate()

    def fake_gh_api_json(endpoint, *, timeout_s=30, headers=None):  # noqa: ANN001
        raise ci_gate.GhApiError("network down")

    monkeypatch.setattr(ci_gate, "_gh_api_json", fake_gh_api_json)

    with caplog.at_level("ERROR"):
        result = ci_gate.wait_for_ci_success(
            "o/r",
            "sha",
            timeout_s=1,
            interval_s=0,
            time_fn=lambda: 0.0,
            sleep_fn=lambda _: None,
        )

    assert result == "failure"
    assert any("ci_gate.api_error" in r.message for r in caplog.records)
