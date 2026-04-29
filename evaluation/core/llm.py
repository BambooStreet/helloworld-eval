import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar

import httpx
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)

from .manifest import RunRecorder
from .models import LLMCallRecord, LLMRole, LLMUsage
from .prompts import Prompt
from .settings import Settings

# gpt-4o-mini pricing (USD per token). Source: OpenAI public pricing page.
# Update here when model is added or prices change.
PRICING: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {
        "input": 0.150 / 1_000_000,
        "output": 0.600 / 1_000_000,
    },
}

T = TypeVar("T")


def is_retryable_error(exc: BaseException) -> bool:
    if isinstance(exc, APIConnectionError | APITimeoutError | RateLimitError):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code >= 500
    if isinstance(exc, httpx.TransportError):
        return True
    return False


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    max_retries: int,
    base_delay: float,
    is_retryable: Callable[[BaseException], bool] = is_retryable_error,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> tuple[T, int]:
    """Run fn() with exponential backoff. Returns (result, attempts_used).

    `max_retries` is the total number of attempts (1 = no retry). Sleep is injectable for tests.
    """
    if max_retries < 1:
        raise ValueError("max_retries must be >= 1")
    last_exc: BaseException | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return await fn(), attempt
        except BaseException as exc:
            last_exc = exc
            if not is_retryable(exc) or attempt >= max_retries:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            await sleep(delay)
    assert last_exc is not None  # for type-narrowing; loop always raises or returns
    raise last_exc


@dataclass
class LLMResult:
    content: str
    record: LLMCallRecord


class LLMClient:
    """Async OpenAI wrapper with timeouts, exponential-backoff retries, role-based concurrency
    semaphores, and per-call cost accounting routed into a RunRecorder."""

    def __init__(self, settings: Settings, recorder: RunRecorder | None = None) -> None:
        self.settings = settings
        self.recorder = recorder
        self._client = AsyncOpenAI()
        self._semaphores: dict[LLMRole, asyncio.Semaphore] = {
            "persona": asyncio.Semaphore(settings.persona_concurrency),
            "judge": asyncio.Semaphore(settings.judge_concurrency),
            "termination": asyncio.Semaphore(settings.judge_concurrency),
            "info_atom": asyncio.Semaphore(settings.judge_concurrency),
            "allowed_atoms": asyncio.Semaphore(settings.judge_concurrency),
            "other": asyncio.Semaphore(settings.judge_concurrency),
        }

    def _timeout_for(self, role: LLMRole) -> float:
        if role == "persona":
            return self.settings.persona_timeout_s
        return self.settings.judge_timeout_s

    @staticmethod
    def cost_usd(usage: LLMUsage, model_id: str) -> float:
        rates = PRICING.get(model_id)
        if rates is None:
            return 0.0
        return usage.prompt_tokens * rates["input"] + usage.completion_tokens * rates["output"]

    async def chat(
        self,
        *,
        role: LLMRole,
        messages: list[dict[str, str]],
        prompt: Prompt | None = None,
        temperature: float = 0.0,
        top_p: float | None = None,
        seed: int | None = None,
        json_mode: bool = False,
        model_id: str | None = None,
    ) -> LLMResult:
        model = model_id or self.settings.model_id
        timeout_s = self._timeout_for(role)
        semaphore = self._semaphores[role]

        async with semaphore:
            start = time.perf_counter()

            kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "timeout": timeout_s,
            }
            if top_p is not None:
                kwargs["top_p"] = top_p
            if seed is not None:
                kwargs["seed"] = seed
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}

            async def call() -> Any:
                return await self._client.chat.completions.create(**kwargs)

            response, attempts = await retry_async(
                call,
                max_retries=self.settings.max_retries,
                base_delay=self.settings.retry_base_delay_s,
            )

            latency_ms = (time.perf_counter() - start) * 1000.0

            usage_obj = response.usage
            usage = LLMUsage(
                prompt_tokens=getattr(usage_obj, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(usage_obj, "completion_tokens", 0) or 0,
                total_tokens=getattr(usage_obj, "total_tokens", 0) or 0,
            )

            content = response.choices[0].message.content or ""

            record = LLMCallRecord(
                timestamp=datetime.now(UTC),
                role=role,
                model_id=model,
                model_version=getattr(response, "system_fingerprint", None),
                prompt_id=prompt.id if prompt else None,
                prompt_version=prompt.version if prompt else None,
                prompt_sha256=prompt.sha256 if prompt else None,
                temperature=temperature,
                top_p=top_p,
                seed=seed,
                response_format="json_object" if json_mode else None,
                usage=usage,
                usd_cost=self.cost_usd(usage, model),
                latency_ms=latency_ms,
                attempts=attempts,
            )

            if self.recorder is not None:
                self.recorder.add_llm_call(record)

            return LLMResult(content=content, record=record)
