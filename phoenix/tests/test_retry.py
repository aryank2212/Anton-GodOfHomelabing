from __future__ import annotations

import asyncio

import pytest

from app.core.retry import RetryExhausted, RetryPolicy, run_with_retry


def test_retry_policy_defaults() -> None:
    policy = RetryPolicy()
    assert policy.attempts == 3
    assert policy.delays() == [5.0, 10.0]


def test_retry_policy_from_dict() -> None:
    policy = RetryPolicy.from_dict({"attempts": 4, "backoff": 1, "multiplier": 3, "max_backoff": 5})
    assert policy.attempts == 4
    assert policy.delays() == [1.0, 3.0, 5.0]


def test_retry_policy_from_none() -> None:
    assert RetryPolicy.from_dict(None) == RetryPolicy()


async def test_run_with_retry_succeeds_on_first_attempt() -> None:
    calls: list[int] = []

    async def fn(attempt: int) -> str:
        calls.append(attempt)
        return "ok"

    result = await run_with_retry(RetryPolicy(attempts=3, backoff=0.01), fn)
    assert result == "ok"
    assert calls == [1]


async def test_run_with_retry_recovers_on_second_attempt() -> None:
    calls: list[int] = []

    async def fn(attempt: int) -> str:
        calls.append(attempt)
        if attempt == 1:
            raise ValueError("boom")
        return "ok"

    result = await run_with_retry(RetryPolicy(attempts=3, backoff=0.01), fn)
    assert result == "ok"
    assert calls == [1, 2]


async def test_run_with_retry_exhausts_and_raises() -> None:
    calls: list[int] = []

    async def fn(attempt: int) -> str:
        calls.append(attempt)
        raise ValueError("boom")

    with pytest.raises(RetryExhausted) as exc_info:
        await run_with_retry(RetryPolicy(attempts=3, backoff=0.01), fn)
    assert exc_info.value.attempts == 3
    assert isinstance(exc_info.value.last_error, ValueError)
    assert calls == [1, 2, 3]


async def test_run_with_retry_honours_backoff_schedule() -> None:
    slept: list[float] = []

    async def fn(attempt: int) -> str:
        raise ValueError("boom")

    async def sleeper(seconds: float) -> None:
        slept.append(seconds)

    original_sleep = asyncio.sleep
    asyncio.sleep = sleeper  # type: ignore[assignment]
    try:
        with pytest.raises(RetryExhausted):
            await run_with_retry(RetryPolicy(attempts=3, backoff=0.5, multiplier=2), fn)
    finally:
        asyncio.sleep = original_sleep  # type: ignore[assignment]

    assert slept == [0.5, 1.0]
