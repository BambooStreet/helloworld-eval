from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from evaluation.core.llm import PRICING, LLMClient
from evaluation.core.manifest import RunRecorder
from evaluation.core.settings import Settings


def make_response(
    content: str = "hello",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.usage = MagicMock()
    resp.usage.prompt_tokens = prompt_tokens
    resp.usage.completion_tokens = completion_tokens
    resp.usage.total_tokens = prompt_tokens + completion_tokens
    resp.system_fingerprint = "fp_test"
    return resp


async def test_chat_records_call_and_computes_cost(tmp_path: Path) -> None:
    settings = Settings(project_root=tmp_path)
    rec = RunRecorder(runs_dir=tmp_path / "runs", project_root=tmp_path)
    client = LLMClient(settings=settings, recorder=rec)
    client._client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
        return_value=make_response("hello", 10, 5)
    )

    result = await client.chat(
        role="other",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.0,
    )

    assert result.content == "hello"
    assert result.record.usage.total_tokens == 15
    assert result.record.attempts == 1
    rates = PRICING["gpt-4o-mini"]
    expected = 10 * rates["input"] + 5 * rates["output"]
    assert result.record.usd_cost == pytest.approx(expected)
    assert len(rec.manifest.llm_calls) == 1


async def test_chat_retries_on_transport_error(tmp_path: Path) -> None:
    settings = Settings(project_root=tmp_path, retry_base_delay_s=0.0, max_retries=3)
    client = LLMClient(settings=settings, recorder=None)

    counter = {"n": 0}

    async def flaky(**_kwargs: object) -> object:
        counter["n"] += 1
        if counter["n"] < 3:
            raise httpx.ConnectError("boom")
        return make_response("ok")

    client._client.chat.completions.create = flaky  # type: ignore[method-assign,assignment]

    result = await client.chat(
        role="other",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert result.content == "ok"
    assert result.record.attempts == 3


async def test_chat_does_not_retry_4xx_other_than_429(tmp_path: Path) -> None:
    from openai import APIStatusError

    settings = Settings(project_root=tmp_path, retry_base_delay_s=0.0, max_retries=3)
    client = LLMClient(settings=settings, recorder=None)

    counter = {"n": 0}

    async def fail_400(**_kwargs: object) -> object:
        counter["n"] += 1
        response = MagicMock(spec=httpx.Response)
        response.status_code = 400
        response.headers = {}
        raise APIStatusError("bad request", response=response, body=None)

    client._client.chat.completions.create = fail_400  # type: ignore[method-assign,assignment]

    with pytest.raises(APIStatusError):
        await client.chat(role="other", messages=[{"role": "user", "content": "hi"}])
    assert counter["n"] == 1


async def test_chat_passes_through_options(tmp_path: Path) -> None:
    settings = Settings(project_root=tmp_path)
    client = LLMClient(settings=settings, recorder=None)
    captured: dict[str, object] = {}

    async def capture(**kwargs: object) -> object:
        captured.update(kwargs)
        return make_response()

    client._client.chat.completions.create = capture  # type: ignore[method-assign,assignment]

    await client.chat(
        role="judge",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.2,
        top_p=0.9,
        seed=42,
        json_mode=True,
    )
    assert captured["model"] == "gpt-4o-mini"
    assert captured["temperature"] == 0.2
    assert captured["top_p"] == 0.9
    assert captured["seed"] == 42
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["timeout"] == settings.judge_timeout_s
