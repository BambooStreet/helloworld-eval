from pathlib import Path

import httpx
import pytest

from evaluation.core.settings import Settings
from evaluation.simulator.adapter import (
    ChatbotAdapterConfig,
    HttpChatbotAdapter,
    MockChatbotAdapter,
    _get_path,
    _substitute,
)


def _make_settings() -> Settings:
    return Settings(retry_base_delay_s=0.0, max_retries=3, chatbot_concurrency=1)


def _make_config(answer_path: str = "answer") -> ChatbotAdapterConfig:
    return ChatbotAdapterConfig(
        endpoint="https://chatbot.example.com/api/question",
        method="POST",
        headers={"Content-Type": "application/json"},
        request_body={"query": "${user_message}", "sessionId": "${session_id}"},
        answer_path=answer_path,
    )


def test_substitute_handles_nested_dicts() -> None:
    body = {"a": "${x}", "b": {"c": "${y}", "d": ["${x}", "static"]}, "e": 1}
    out = _substitute(body, {"x": "X", "y": "Y"})
    assert out == {"a": "X", "b": {"c": "Y", "d": ["X", "static"]}, "e": 1}


def test_get_path_traverses_dot_path() -> None:
    data = {"a": {"b": {"c": "found"}}}
    assert _get_path(data, "a.b.c") == "found"
    assert _get_path(data, "") == data


def test_get_path_missing_raises() -> None:
    with pytest.raises(KeyError):
        _get_path({"a": 1}, "a.b")


async def test_http_adapter_success_extracts_answer_and_substitutes() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"answer": "안녕하세요"})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    adapter = HttpChatbotAdapter(_make_config(), _make_settings(), client=client)

    result = await adapter.send(session_id="sess-1", user_message="안녕")
    assert result.content == "안녕하세요"
    assert result.latency_ms >= 0
    import json as _json

    body = _json.loads(captured["body"])  # type: ignore[arg-type]
    assert body == {"query": "안녕", "sessionId": "sess-1"}
    await adapter.aclose()


async def test_http_adapter_retries_on_500() -> None:
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        if counter["n"] < 3:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"answer": "ok"})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    adapter = HttpChatbotAdapter(_make_config(), _make_settings(), client=client)

    result = await adapter.send(session_id="s", user_message="hi")
    assert result.content == "ok"
    assert counter["n"] == 3
    await adapter.aclose()


async def test_http_adapter_does_not_retry_400() -> None:
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        return httpx.Response(400, json={"error": "bad"})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    adapter = HttpChatbotAdapter(_make_config(), _make_settings(), client=client)

    with pytest.raises(httpx.HTTPStatusError):
        await adapter.send(session_id="s", user_message="hi")
    assert counter["n"] == 1
    await adapter.aclose()


async def test_http_adapter_retries_on_429() -> None:
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        if counter["n"] < 2:
            return httpx.Response(429, text="rate limited")
        return httpx.Response(200, json={"answer": "ok"})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    adapter = HttpChatbotAdapter(_make_config(), _make_settings(), client=client)

    result = await adapter.send(session_id="s", user_message="hi")
    assert result.content == "ok"
    assert counter["n"] == 2
    await adapter.aclose()


async def test_http_adapter_dotpath_extracts_nested_answer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"answer": "deep"}})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    adapter = HttpChatbotAdapter(
        _make_config(answer_path="data.answer"), _make_settings(), client=client
    )
    result = await adapter.send(session_id="s", user_message="hi")
    assert result.content == "deep"
    await adapter.aclose()


def test_adapter_config_loads_from_yaml(tmp_path: Path) -> None:
    cfg = tmp_path / "adapter.yaml"
    cfg.write_text(
        "endpoint: https://x.example.com/api/question\n"
        "method: POST\n"
        "headers:\n"
        "  Content-Type: application/json\n"
        "request_body:\n"
        "  query: ${user_message}\n"
        "  sessionId: ${session_id}\n"
        "answer_path: answer\n",
        encoding="utf-8",
    )
    adapter = HttpChatbotAdapter.from_yaml(cfg, _make_settings())
    assert adapter.config.endpoint.endswith("/api/question")
    assert adapter.config.answer_path == "answer"


async def test_mock_adapter_cycles_responses() -> None:
    adapter = MockChatbotAdapter(responses=["a", "b"], fixed_latency_ms=10.0)
    r1 = await adapter.send(session_id="s", user_message="m1")
    r2 = await adapter.send(session_id="s", user_message="m2")
    r3 = await adapter.send(session_id="s", user_message="m3")
    assert r1.content == "a"
    assert r2.content == "b"
    # Stays on last response after exhausting list
    assert r3.content == "b"
    assert r1.latency_ms == 10.0
