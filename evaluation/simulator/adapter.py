import asyncio
import time
from pathlib import Path
from string import Template
from typing import Any, ClassVar, Protocol, runtime_checkable

import httpx
import yaml
from pydantic import BaseModel, Field

from ..core.llm import retry_async
from ..core.settings import Settings


class ChatbotAdapterConfig(BaseModel):
    endpoint: str
    method: str = "POST"
    headers: dict[str, str] = Field(default_factory=dict)
    request_body: dict[str, Any]
    answer_path: str
    timeout_s: float | None = None


class ChatbotResponse(BaseModel):
    content: str
    latency_ms: float
    raw: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class ChatbotAdapter(Protocol):
    async def send(self, *, session_id: str, user_message: str) -> ChatbotResponse: ...

    async def aclose(self) -> None: ...


def _substitute(value: Any, ctx: dict[str, str]) -> Any:
    if isinstance(value, str):
        return Template(value).safe_substitute(ctx)
    if isinstance(value, dict):
        return {k: _substitute(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, ctx) for v in value]
    return value


def _get_path(data: Any, path: str) -> Any:
    if not path:
        return data
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(f"Path '{path}' not found in response: {data!r}")
        cur = cur[part]
    return cur


def _is_retryable_chatbot_error(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException | httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        sc = exc.response.status_code
        return sc >= 500 or sc == 429
    return False


class HttpChatbotAdapter:
    """YAML config-driven HTTP adapter. Substitutes ${user_message} and ${session_id}
    placeholders in request_body, retries on 5xx/429/transport errors with exponential
    backoff, and extracts the answer via dot-path."""

    def __init__(
        self,
        config: ChatbotAdapterConfig,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.settings = settings
        self._client = client or httpx.AsyncClient()
        self._semaphore = asyncio.Semaphore(settings.chatbot_concurrency)

    @classmethod
    def from_yaml(
        cls,
        path: Path,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
    ) -> "HttpChatbotAdapter":
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        config = ChatbotAdapterConfig.model_validate(data)
        return cls(config=config, settings=settings, client=client)

    async def send(self, *, session_id: str, user_message: str) -> ChatbotResponse:
        ctx = {"user_message": user_message, "session_id": session_id}
        body = _substitute(self.config.request_body, ctx)
        timeout = self.config.timeout_s or self.settings.chatbot_timeout_s

        async with self._semaphore:
            start = time.perf_counter()

            async def call() -> httpx.Response:
                response = await self._client.request(
                    method=self.config.method,
                    url=self.config.endpoint,
                    headers=self.config.headers,
                    json=body,
                    timeout=timeout,
                )
                if response.status_code >= 500 or response.status_code == 429:
                    raise httpx.HTTPStatusError(
                        f"retryable status {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                return response

            response, _attempts = await retry_async(
                call,
                max_retries=self.settings.max_retries,
                base_delay=self.settings.retry_base_delay_s,
                is_retryable=_is_retryable_chatbot_error,
            )
            latency_ms = (time.perf_counter() - start) * 1000.0

        response.raise_for_status()
        data = response.json()
        content = _get_path(data, self.config.answer_path)
        return ChatbotResponse(content=str(content), latency_ms=latency_ms, raw=data)

    async def aclose(self) -> None:
        await self._client.aclose()


class MockChatbotAdapter:
    """In-memory deterministic adapter. Cycles through canned responses with fixed
    latency so re-running with a temp=0 persona produces identical conversations."""

    DEFAULT_RESPONSES: ClassVar[list[str]] = [
        "안녕하세요. 무엇을 도와드릴까요?",
        "조금 더 구체적으로 말씀해 주실 수 있을까요?",
        "관련 절차는 다음과 같습니다. 1) 신청서 작성 2) 서류 제출 3) 심사",
        "추가로 궁금하신 부분이 있으시면 말씀해 주세요.",
        "도움이 되었기를 바랍니다. 다른 질문이 있으시면 알려주세요.",
    ]

    def __init__(
        self,
        responses: list[str] | None = None,
        fixed_latency_ms: float = 50.0,
    ) -> None:
        self._responses = responses if responses is not None else self.DEFAULT_RESPONSES
        self._fixed_latency = fixed_latency_ms
        self._call_idx = 0

    async def send(self, *, session_id: str, user_message: str) -> ChatbotResponse:
        idx = min(self._call_idx, len(self._responses) - 1)
        content = self._responses[idx]
        self._call_idx += 1
        return ChatbotResponse(
            content=content,
            latency_ms=self._fixed_latency,
            raw={"answer": content, "_session_id": session_id, "_received": user_message},
        )

    async def aclose(self) -> None:
        return None
