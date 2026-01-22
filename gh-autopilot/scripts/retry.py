#!/usr/bin/env python3
"""
gh-autopilot 重试逻辑模块。

实现指数退避重试策略，区分可重试和不可重试错误。
"""

import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import Any, Callable, Optional, Type, Union

# 配置日志
logger = logging.getLogger("gh-autopilot.retry")


class ErrorCategory(str, Enum):
    """错误分类"""
    TRANSIENT = "transient"      # 瞬态错误，可重试 (5xx, 网络错误, 超时)
    RATE_LIMIT = "rate_limit"    # 速率限制，可重试但需更长等待
    CLIENT = "client"            # 客户端错误，不可重试 (4xx 除 429)
    PERMANENT = "permanent"      # 永久错误，不可重试


@dataclass
class RetryPolicy:
    """
    重试策略配置。

    Attributes:
        max_retries: 最大重试次数
        base_delay: 基础延迟时间（秒）
        max_delay: 最大延迟时间（秒）
        exponential_base: 指数退避的底数
        jitter: 是否添加随机抖动
        jitter_factor: 抖动因子 (0-1)，控制随机范围
        retryable_exceptions: 可重试的异常类型列表
        retry_on_result: 结果判断函数，返回 True 则重试
    """
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    jitter: bool = True
    jitter_factor: float = 0.5
    retryable_exceptions: tuple = field(default_factory=lambda: (Exception,))
    retry_on_result: Optional[Callable[[Any], bool]] = None

    def calculate_delay(self, attempt: int) -> float:
        """
        计算第 N 次重试的延迟时间。

        使用指数退避算法: delay = base_delay * (exponential_base ** attempt)
        可选添加随机抖动以避免惊群效应。

        Args:
            attempt: 当前重试次数 (0-indexed)

        Returns:
            延迟时间（秒）
        """
        # 指数退避计算
        delay = self.base_delay * (self.exponential_base ** attempt)

        # 限制最大延迟
        delay = min(delay, self.max_delay)

        # 添加随机抖动
        if self.jitter:
            jitter_range = delay * self.jitter_factor
            delay = delay + random.uniform(-jitter_range, jitter_range)
            delay = max(0.1, delay)  # 确保延迟不为负或过小

        return delay

    def should_retry(self, exception: Exception) -> bool:
        """
        判断异常是否可重试。

        Args:
            exception: 捕获的异常

        Returns:
            是否应该重试
        """
        return isinstance(exception, self.retryable_exceptions)


@dataclass
class RetryResult:
    """重试执行结果"""
    success: bool
    result: Any = None
    exception: Optional[Exception] = None
    attempts: int = 0
    total_delay: float = 0.0
    error_category: Optional[ErrorCategory] = None


class RetryableError(Exception):
    """可重试的错误基类"""

    def __init__(self, message: str, category: ErrorCategory = ErrorCategory.TRANSIENT):
        super().__init__(message)
        self.category = category


class TransientError(RetryableError):
    """瞬态错误 - 可重试"""

    def __init__(self, message: str):
        super().__init__(message, ErrorCategory.TRANSIENT)


class RateLimitError(RetryableError):
    """速率限制错误 - 需要更长等待时间"""

    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message, ErrorCategory.RATE_LIMIT)
        self.retry_after = retry_after


class ClientError(Exception):
    """客户端错误 - 不可重试"""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code
        self.category = ErrorCategory.CLIENT


class PermanentError(Exception):
    """永久错误 - 不可重试"""

    def __init__(self, message: str):
        super().__init__(message)
        self.category = ErrorCategory.PERMANENT


def categorize_error(exception: Exception) -> ErrorCategory:
    """
    根据异常类型判断错误类别。

    Args:
        exception: 捕获的异常

    Returns:
        错误类别
    """
    # 已明确分类的错误
    if hasattr(exception, 'category'):
        return exception.category

    error_msg = str(exception).lower()

    # HTTP 状态码判断
    if hasattr(exception, 'status_code'):
        status = exception.status_code
        if status == 429:
            return ErrorCategory.RATE_LIMIT
        elif 400 <= status < 500:
            return ErrorCategory.CLIENT
        elif 500 <= status < 600:
            return ErrorCategory.TRANSIENT

    # 网络相关错误 - 可重试
    network_keywords = [
        'timeout', 'connection', 'network', 'socket',
        'refused', 'reset', 'unreachable', 'temporary',
        'unavailable', '503', '502', '504', '500'
    ]
    if any(kw in error_msg for kw in network_keywords):
        return ErrorCategory.TRANSIENT

    # 速率限制
    rate_limit_keywords = ['rate limit', 'too many requests', '429', 'throttl']
    if any(kw in error_msg for kw in rate_limit_keywords):
        return ErrorCategory.RATE_LIMIT

    # 客户端错误关键字
    client_keywords = ['not found', '404', 'unauthorized', '401', 'forbidden', '403', 'bad request', '400']
    if any(kw in error_msg for kw in client_keywords):
        return ErrorCategory.CLIENT

    # 默认为瞬态错误（可重试）
    return ErrorCategory.TRANSIENT


def is_retryable(exception: Exception) -> bool:
    """
    判断异常是否可重试。

    Args:
        exception: 捕获的异常

    Returns:
        是否可重试
    """
    category = categorize_error(exception)
    return category in (ErrorCategory.TRANSIENT, ErrorCategory.RATE_LIMIT)


class RetryExecutor:
    """
    重试执行器。

    封装重试逻辑，支持主方法和降级方法。
    """

    def __init__(
        self,
        policy: Optional[RetryPolicy] = None,
        on_retry: Optional[Callable[[int, Exception, float], None]] = None,
        on_failure: Optional[Callable[[Exception, int], None]] = None,
    ):
        """
        初始化重试执行器。

        Args:
            policy: 重试策略，默认使用 RetryPolicy()
            on_retry: 重试回调函数 (attempt, exception, delay)
            on_failure: 最终失败回调函数 (exception, total_attempts)
        """
        self.policy = policy or RetryPolicy()
        self.on_retry = on_retry
        self.on_failure = on_failure

    def execute(
        self,
        func: Callable[..., Any],
        *args,
        fallback: Optional[Callable[..., Any]] = None,
        fallback_args: Optional[tuple] = None,
        fallback_kwargs: Optional[dict] = None,
        **kwargs,
    ) -> RetryResult:
        """
        执行函数，失败时自动重试。

        Args:
            func: 要执行的主函数
            *args: 函数参数
            fallback: 降级函数（主函数重试耗尽后调用）
            fallback_args: 降级函数的参数
            fallback_kwargs: 降级函数的关键字参数
            **kwargs: 函数关键字参数

        Returns:
            RetryResult 包含执行结果或错误信息
        """
        last_exception: Optional[Exception] = None
        total_delay = 0.0

        for attempt in range(self.policy.max_retries + 1):
            try:
                result = func(*args, **kwargs)

                # 检查结果是否需要重试
                if self.policy.retry_on_result and self.policy.retry_on_result(result):
                    if attempt < self.policy.max_retries:
                        delay = self.policy.calculate_delay(attempt)
                        total_delay += delay
                        logger.info(f"结果不满足条件，{delay:.2f}s 后重试 (尝试 {attempt + 1}/{self.policy.max_retries + 1})")
                        if self.on_retry:
                            self.on_retry(attempt, ValueError("Result check failed"), delay)
                        time.sleep(delay)
                        continue

                return RetryResult(
                    success=True,
                    result=result,
                    attempts=attempt + 1,
                    total_delay=total_delay,
                )

            except Exception as e:
                last_exception = e
                error_category = categorize_error(e)

                logger.warning(f"执行失败 (尝试 {attempt + 1}/{self.policy.max_retries + 1}): {e}")

                # 不可重试的错误，立即返回
                if not is_retryable(e):
                    logger.error(f"不可重试的错误 ({error_category.value}): {e}")
                    if self.on_failure:
                        self.on_failure(e, attempt + 1)
                    return RetryResult(
                        success=False,
                        exception=e,
                        attempts=attempt + 1,
                        total_delay=total_delay,
                        error_category=error_category,
                    )

                # 还有重试机会
                if attempt < self.policy.max_retries:
                    # 计算延迟
                    if isinstance(e, RateLimitError) and e.retry_after:
                        delay = e.retry_after
                    else:
                        delay = self.policy.calculate_delay(attempt)

                    total_delay += delay
                    logger.info(f"{delay:.2f}s 后重试...")

                    if self.on_retry:
                        self.on_retry(attempt, e, delay)

                    time.sleep(delay)

        # 主函数重试耗尽，尝试降级函数
        if fallback is not None:
            logger.info("主方法失败，尝试降级方法...")
            try:
                fb_args = fallback_args or args
                fb_kwargs = fallback_kwargs or kwargs
                result = fallback(*fb_args, **fb_kwargs)
                return RetryResult(
                    success=True,
                    result=result,
                    attempts=self.policy.max_retries + 2,  # +1 for fallback
                    total_delay=total_delay,
                )
            except Exception as e:
                logger.error(f"降级方法也失败: {e}")
                last_exception = e

        # 完全失败
        if self.on_failure and last_exception:
            self.on_failure(last_exception, self.policy.max_retries + 1)

        return RetryResult(
            success=False,
            exception=last_exception,
            attempts=self.policy.max_retries + 1,
            total_delay=total_delay,
            error_category=categorize_error(last_exception) if last_exception else None,
        )


def with_retry(
    policy: Optional[RetryPolicy] = None,
    fallback: Optional[Callable] = None,
    on_retry: Optional[Callable[[int, Exception, float], None]] = None,
):
    """
    重试装饰器。

    使用示例:
        @with_retry(RetryPolicy(max_retries=3, base_delay=1.0))
        def fetch_data():
            ...

    Args:
        policy: 重试策略
        fallback: 降级函数
        on_retry: 重试回调

    Returns:
        装饰器函数
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            executor = RetryExecutor(policy=policy, on_retry=on_retry)
            result = executor.execute(func, *args, fallback=fallback, **kwargs)

            if result.success:
                return result.result
            else:
                raise result.exception

        return wrapper
    return decorator


# 预定义的重试策略
DEFAULT_RETRY_POLICY = RetryPolicy(
    max_retries=3,
    base_delay=1.0,
    max_delay=30.0,
    jitter=True,
)

AGGRESSIVE_RETRY_POLICY = RetryPolicy(
    max_retries=5,
    base_delay=0.5,
    max_delay=60.0,
    jitter=True,
)

CONSERVATIVE_RETRY_POLICY = RetryPolicy(
    max_retries=2,
    base_delay=2.0,
    max_delay=10.0,
    jitter=True,
)

API_RETRY_POLICY = RetryPolicy(
    max_retries=3,
    base_delay=1.0,
    max_delay=30.0,
    exponential_base=2.0,
    jitter=True,
    jitter_factor=0.25,
)
