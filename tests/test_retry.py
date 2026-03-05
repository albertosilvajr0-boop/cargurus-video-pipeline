"""Tests for the retry utility module."""

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from utils.retry import retry_sync, retry_async, is_retryable, RetryError


class TestIsRetryable:
    def test_rate_limit_error(self):
        assert is_retryable(Exception("rate limit exceeded")) is True

    def test_429_error(self):
        assert is_retryable(Exception("HTTP 429 Too Many Requests")) is True

    def test_500_error(self):
        assert is_retryable(Exception("Internal Server Error 500")) is True

    def test_502_error(self):
        assert is_retryable(Exception("502 Bad Gateway")) is True

    def test_503_error(self):
        assert is_retryable(Exception("503 Service Unavailable")) is True

    def test_timeout_error(self):
        assert is_retryable(Exception("Connection timed out")) is True

    def test_connection_error(self):
        assert is_retryable(Exception("Connection reset by peer")) is True

    def test_quota_error(self):
        assert is_retryable(Exception("Resource exhausted: quota exceeded")) is True

    def test_openai_overloaded(self):
        assert is_retryable(Exception("server_error: overloaded")) is True

    def test_non_retryable(self):
        assert is_retryable(Exception("Invalid API key")) is False

    def test_permission_error(self):
        assert is_retryable(Exception("403 Forbidden")) is False

    def test_not_found(self):
        assert is_retryable(Exception("404 Not Found")) is False


class TestRetrySyncDecorator:
    def test_succeeds_first_try(self):
        call_count = 0

        @retry_sync(max_retries=3, base_delay=0.01)
        def good_func():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = good_func()
        assert result == "ok"
        assert call_count == 1

    def test_retries_on_transient_error(self):
        call_count = 0

        @retry_sync(max_retries=3, base_delay=0.01)
        def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("503 Service Unavailable")
            return "recovered"

        result = flaky_func()
        assert result == "recovered"
        assert call_count == 3

    def test_raises_on_non_retryable(self):
        @retry_sync(max_retries=3, base_delay=0.01)
        def bad_func():
            raise ValueError("Invalid input")

        with pytest.raises(ValueError, match="Invalid input"):
            bad_func()

    def test_exhausts_retries(self):
        @retry_sync(max_retries=2, base_delay=0.01)
        def always_fails():
            raise Exception("503 Service Unavailable")

        with pytest.raises(Exception, match="503"):
            always_fails()


class TestRetryAsyncDecorator:
    @pytest.mark.asyncio
    async def test_succeeds_first_try(self):
        call_count = 0

        @retry_async(max_retries=3, base_delay=0.01)
        async def good_func():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await good_func()
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_transient_error(self):
        call_count = 0

        @retry_async(max_retries=3, base_delay=0.01)
        async def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("429 Too Many Requests")
            return "recovered"

        result = await flaky_func()
        assert result == "recovered"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_raises_on_non_retryable(self):
        @retry_async(max_retries=3, base_delay=0.01)
        async def bad_func():
            raise ValueError("Bad request")

        with pytest.raises(ValueError, match="Bad request"):
            await bad_func()

    @pytest.mark.asyncio
    async def test_exhausts_retries(self):
        @retry_async(max_retries=2, base_delay=0.01)
        async def always_fails():
            raise Exception("rate limit exceeded")

        with pytest.raises(Exception, match="rate limit"):
            await always_fails()
