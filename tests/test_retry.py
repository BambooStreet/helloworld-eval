import pytest

from evaluation.core.llm import retry_async


class FakeRetryable(Exception):
    pass


class FakeFatal(Exception):
    pass


def _is_retry(exc: BaseException) -> bool:
    return isinstance(exc, FakeRetryable)


async def _no_sleep(_seconds: float) -> None:
    return None


async def test_retry_succeeds_on_first_attempt() -> None:
    async def fn() -> int:
        return 42

    result, attempts = await retry_async(
        fn, max_retries=3, base_delay=0.0, is_retryable=_is_retry, sleep=_no_sleep
    )
    assert result == 42
    assert attempts == 1


async def test_retry_succeeds_after_some_failures() -> None:
    counter = {"n": 0}

    async def fn() -> str:
        counter["n"] += 1
        if counter["n"] < 3:
            raise FakeRetryable()
        return "ok"

    result, attempts = await retry_async(
        fn, max_retries=5, base_delay=0.0, is_retryable=_is_retry, sleep=_no_sleep
    )
    assert result == "ok"
    assert attempts == 3
    assert counter["n"] == 3


async def test_retry_gives_up_after_max_attempts() -> None:
    counter = {"n": 0}

    async def fn() -> str:
        counter["n"] += 1
        raise FakeRetryable()

    with pytest.raises(FakeRetryable):
        await retry_async(
            fn, max_retries=3, base_delay=0.0, is_retryable=_is_retry, sleep=_no_sleep
        )
    assert counter["n"] == 3


async def test_retry_does_not_retry_fatal_errors() -> None:
    counter = {"n": 0}

    async def fn() -> str:
        counter["n"] += 1
        raise FakeFatal()

    with pytest.raises(FakeFatal):
        await retry_async(
            fn, max_retries=5, base_delay=0.0, is_retryable=_is_retry, sleep=_no_sleep
        )
    assert counter["n"] == 1


async def test_retry_invalid_max_retries() -> None:
    async def fn() -> int:
        return 1

    with pytest.raises(ValueError):
        await retry_async(fn, max_retries=0, base_delay=0.0)


async def test_retry_uses_exponential_backoff() -> None:
    delays: list[float] = []

    async def fake_sleep(d: float) -> None:
        delays.append(d)

    async def fn() -> str:
        raise FakeRetryable()

    with pytest.raises(FakeRetryable):
        await retry_async(
            fn,
            max_retries=4,
            base_delay=1.0,
            is_retryable=_is_retry,
            sleep=fake_sleep,
        )
    # 3 sleeps between 4 attempts: 1, 2, 4
    assert delays == [1.0, 2.0, 4.0]
