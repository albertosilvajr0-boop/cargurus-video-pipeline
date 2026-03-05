"""Retry utilities with exponential backoff for API calls."""

import asyncio
import functools
import time
from typing import Callable

from rich.console import Console

console = Console()

# Default retry configuration
DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_DELAY = 2.0  # seconds
DEFAULT_MAX_DELAY = 30.0  # seconds
DEFAULT_BACKOFF_FACTOR = 2.0

# Exceptions that are worth retrying (transient errors)
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class RetryError(Exception):
    """Raised when all retry attempts are exhausted."""

    def __init__(self, message: str, last_exception: Exception = None):
        super().__init__(message)
        self.last_exception = last_exception


def is_retryable(exc: Exception) -> bool:
    """Determine if an exception is worth retrying."""
    exc_str = str(exc).lower()

    # Rate limiting
    if "rate" in exc_str and "limit" in exc_str:
        return True
    if "429" in exc_str or "too many requests" in exc_str:
        return True

    # Server errors
    if any(code in exc_str for code in ["500", "502", "503", "504"]):
        return True

    # Timeout / connection errors
    if any(kw in exc_str for kw in ["timeout", "timed out", "connection", "reset", "broken pipe"]):
        return True

    # Google API specific
    if "resource exhausted" in exc_str or "quota" in exc_str:
        return True

    # OpenAI specific
    if "server_error" in exc_str or "overloaded" in exc_str:
        return True

    # httpx / network errors
    for exc_type_name in ("ConnectError", "ReadTimeout", "WriteTimeout", "PoolTimeout"):
        if exc_type_name in type(exc).__name__:
            return True

    return False


def retry_sync(
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    retryable_check: Callable[[Exception], bool] = None,
    operation_name: str = "",
):
    """Decorator for synchronous functions with retry and exponential backoff."""

    check = retryable_check or is_retryable

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            name = operation_name or func.__name__

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exc = e

                    if attempt >= max_retries or not check(e):
                        raise

                    delay = min(base_delay * (backoff_factor ** attempt), max_delay)
                    console.print(
                        f"[yellow]  Retry {attempt + 1}/{max_retries} for {name} "
                        f"(waiting {delay:.1f}s): {e}[/yellow]"
                    )
                    time.sleep(delay)

            raise RetryError(f"All {max_retries} retries exhausted for {name}", last_exc)

        return wrapper
    return decorator


def retry_async(
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    retryable_check: Callable[[Exception], bool] = None,
    operation_name: str = "",
):
    """Decorator for async functions with retry and exponential backoff."""

    check = retryable_check or is_retryable

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exc = None
            name = operation_name or func.__name__

            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exc = e

                    if attempt >= max_retries or not check(e):
                        raise

                    delay = min(base_delay * (backoff_factor ** attempt), max_delay)
                    console.print(
                        f"[yellow]  Retry {attempt + 1}/{max_retries} for {name} "
                        f"(waiting {delay:.1f}s): {e}[/yellow]"
                    )
                    await asyncio.sleep(delay)

            raise RetryError(f"All {max_retries} retries exhausted for {name}", last_exc)

        return wrapper
    return decorator
