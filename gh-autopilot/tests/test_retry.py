#!/usr/bin/env python3
"""
retry.py 单元测试（覆盖率补齐）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

# 添加 scripts 目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from retry import (  # noqa: E402
    ClientError,
    ErrorCategory,
    RateLimitError,
    RetryExecutor,
    RetryPolicy,
    TransientError,
    categorize_error,
    is_retryable,
    with_retry,
)


def test_retry_policy_calculate_delay_with_jitter(monkeypatch):
    policy = RetryPolicy(base_delay=1.0, exponential_base=2.0, max_delay=10.0, jitter=True, jitter_factor=0.5)
    monkeypatch.setattr("random.uniform", lambda a, b: 0.0)
    assert policy.calculate_delay(0) == 1.0
    assert policy.calculate_delay(1) == 2.0


def test_retry_policy_should_retry():
    policy = RetryPolicy(retryable_exceptions=(ValueError,))
    assert policy.should_retry(ValueError("x")) is True
    assert policy.should_retry(RuntimeError("x")) is False


def test_categorize_error_prefers_category_attribute():
    err = TransientError("timeout")
    assert categorize_error(err) == ErrorCategory.TRANSIENT


def test_categorize_error_by_status_code():
    class HttpError(Exception):
        def __init__(self, status_code: int):
            super().__init__(f"HTTP {status_code}")
            self.status_code = status_code

    assert categorize_error(HttpError(429)) == ErrorCategory.RATE_LIMIT
    assert categorize_error(HttpError(404)) == ErrorCategory.CLIENT
    assert categorize_error(HttpError(500)) == ErrorCategory.TRANSIENT


def test_categorize_error_by_message_keywords():
    assert categorize_error(Exception("connection reset")) == ErrorCategory.TRANSIENT
    assert categorize_error(Exception("too many requests")) == ErrorCategory.RATE_LIMIT
    assert categorize_error(Exception("forbidden")) == ErrorCategory.CLIENT


def test_is_retryable_matches_categories():
    assert is_retryable(TransientError("x")) is True
    assert is_retryable(RateLimitError("x")) is True
    assert is_retryable(ClientError("x")) is False


def test_retry_executor_retries_on_result(monkeypatch):
    calls = {"n": 0}

    def func():
        calls["n"] += 1
        return "bad" if calls["n"] == 1 else "good"

    policy = RetryPolicy(
        max_retries=1,
        base_delay=1.0,
        jitter=False,
        retry_on_result=lambda r: r == "bad",
    )

    on_retry = Mock()
    monkeypatch.setattr("time.sleep", lambda s: None)

    result = RetryExecutor(policy=policy, on_retry=on_retry).execute(func)
    assert result.success is True
    assert result.result == "good"
    assert result.attempts == 2
    assert on_retry.called is True


def test_retry_executor_non_retryable_calls_on_failure(monkeypatch):
    policy = RetryPolicy(max_retries=2, jitter=False)
    on_failure = Mock()
    monkeypatch.setattr("time.sleep", lambda s: None)

    def func():
        raise ClientError("bad request", status_code=400)

    result = RetryExecutor(policy=policy, on_failure=on_failure).execute(func)
    assert result.success is False
    assert result.error_category == ErrorCategory.CLIENT
    assert on_failure.called is True


def test_retry_executor_rate_limit_uses_retry_after(monkeypatch):
    policy = RetryPolicy(max_retries=1, jitter=False)
    monkeypatch.setattr("time.sleep", lambda s: None)

    calls = {"n": 0}

    def func():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RateLimitError("rate limit", retry_after=5.0)
        return "ok"

    with patch.object(policy, "calculate_delay", return_value=123.0) as calc:
        result = RetryExecutor(policy=policy).execute(func)
    assert result.success is True
    assert result.result == "ok"
    # retry_after 命中时不会调用 calculate_delay
    calc.assert_not_called()


def test_retry_executor_fallback_after_exhausted(monkeypatch):
    policy = RetryPolicy(max_retries=0, jitter=False)
    monkeypatch.setattr("time.sleep", lambda s: None)

    def func():
        raise TransientError("temporary")

    def fallback():
        return "fb"

    result = RetryExecutor(policy=policy).execute(func, fallback=fallback)
    assert result.success is True
    assert result.result == "fb"


def test_with_retry_decorator_success_and_fallback(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)

    @with_retry(policy=RetryPolicy(max_retries=0, jitter=False))
    def ok():
        return 123

    assert ok() == 123

    def fb():
        return "fallback"

    @with_retry(policy=RetryPolicy(max_retries=0, jitter=False), fallback=fb)
    def always_fail():
        raise TransientError("x")

    assert always_fail() == "fallback"


def test_with_retry_decorator_raises_when_no_fallback(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)

    @with_retry(policy=RetryPolicy(max_retries=0, jitter=False))
    def fail():
        raise ClientError("bad")

    with pytest.raises(ClientError):
        fail()

