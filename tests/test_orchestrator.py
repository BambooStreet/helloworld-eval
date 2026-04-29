"""Orchestrator tests use a stub LLM and the in-memory MockChatbotAdapter so the
full conversation loop runs deterministically without any network or API key."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from evaluation.core.llm import LLMResult
from evaluation.core.models import LLMCallRecord, LLMUsage
from evaluation.core.prompts import Prompt
from evaluation.personas.schema import Persona
from evaluation.simulator.adapter import ChatbotResponse, MockChatbotAdapter
from evaluation.simulator.orchestrator import (
    make_session_id,
    run_conversation,
)
from tests.test_persona_schema import _full_persona_dict


class StubLLM:
    """Returns canned `responses` in order. Records call kwargs for assertions."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._idx = 0
        self.calls: list[dict[str, Any]] = []

    async def chat(self, **kwargs: Any) -> LLMResult:
        self.calls.append(kwargs)
        if self._idx >= len(self._responses):
            raise RuntimeError("StubLLM exhausted")
        content = self._responses[self._idx]
        self._idx += 1
        record = LLMCallRecord(
            timestamp=datetime.now(UTC),
            role="persona",
            model_id="stub",
            temperature=0.0,
            usage=LLMUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            usd_cost=0.0,
            latency_ms=1.0,
        )
        return LLMResult(content=content, record=record)


class FailingAdapter:
    """Adapter that raises on send. Used to test failure path."""

    async def send(self, *, session_id: str, user_message: str) -> ChatbotResponse:
        raise RuntimeError("upstream chatbot down")

    async def aclose(self) -> None:
        return None


def _persona() -> Persona:
    return Persona.model_validate(_full_persona_dict())


def _prompt() -> Prompt:
    return Prompt(
        id="persona_user",
        version="v1",
        body="페르소나: ${name} / 목표: ${goal}",
        sha256="a" * 64,
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


async def test_run_conversation_max_turns_writes_jsonl(tmp_path: Path) -> None:
    persona = _persona()
    llm = StubLLM(["안녕하세요", "감사합니다", "한 가지 더"])
    adapter = MockChatbotAdapter(responses=["답변1", "답변2", "답변3"])
    log_path = tmp_path / "conv.jsonl"

    result = await run_conversation(
        persona=persona,
        persona_prompt=_prompt(),
        adapter=adapter,
        llm=llm,
        run_id="run-test",
        max_turns=3,
        log_path=log_path,
    )

    assert result.status == "max_turns"
    assert result.total_user_turns == 3
    assert result.total_bot_turns == 3
    assert result.session_id == "eval-run-test-p_test"

    lines = _read_jsonl(log_path)
    assert len(lines) == 6
    assert lines[0]["role"] == "user"
    assert lines[0]["content"] == "안녕하세요"
    assert lines[0]["tokens"] == {"prompt": 10, "completion": 5, "total": 15}
    assert lines[0]["latency_ms"] == 1.0
    assert "timestamp" in lines[0]
    assert lines[1]["role"] == "bot"
    assert lines[1]["content"] == "답변1"
    assert lines[1]["tokens"] is None
    assert lines[1]["latency_ms"] == 50.0


async def test_run_conversation_chatbot_failure_partial_log(tmp_path: Path) -> None:
    persona = _persona()
    llm = StubLLM(["안녕하세요", "두번째"])
    adapter = FailingAdapter()
    log_path = tmp_path / "conv.jsonl"

    result = await run_conversation(
        persona=persona,
        persona_prompt=_prompt(),
        adapter=adapter,
        llm=llm,
        run_id="run-x",
        max_turns=5,
        log_path=log_path,
    )

    assert result.status == "failed"
    assert result.failure_reason is not None
    assert "chatbot error" in result.failure_reason
    # First user turn was logged before chatbot failure
    lines = _read_jsonl(log_path)
    assert len(lines) == 1
    assert lines[0]["role"] == "user"
    assert result.total_user_turns == 1
    assert result.total_bot_turns == 0


async def test_run_conversation_empty_persona_utterance_fails(tmp_path: Path) -> None:
    persona = _persona()
    llm = StubLLM(["   "])  # whitespace only
    adapter = MockChatbotAdapter()
    log_path = tmp_path / "conv.jsonl"

    result = await run_conversation(
        persona=persona,
        persona_prompt=_prompt(),
        adapter=adapter,
        llm=llm,
        run_id="r",
        max_turns=3,
        log_path=log_path,
    )

    assert result.status == "failed"
    assert "empty" in (result.failure_reason or "")
    assert _read_jsonl(log_path) == []


async def test_run_conversation_uses_explicit_session_id(tmp_path: Path) -> None:
    llm = StubLLM(["hi"])
    adapter = MockChatbotAdapter()
    log_path = tmp_path / "conv.jsonl"

    result = await run_conversation(
        persona=_persona(),
        persona_prompt=_prompt(),
        adapter=adapter,
        llm=llm,
        run_id="r",
        max_turns=1,
        log_path=log_path,
        session_id="custom-sid",
    )
    assert result.session_id == "custom-sid"


async def test_run_conversation_persona_history_accumulates(tmp_path: Path) -> None:
    """Verify the persona LLM sees prior bot replies as 'user' role messages,
    so multi-turn coherence is possible."""
    llm = StubLLM(["첫발화", "두번째발화"])
    adapter = MockChatbotAdapter(responses=["봇1", "봇2"])
    log_path = tmp_path / "conv.jsonl"

    await run_conversation(
        persona=_persona(),
        persona_prompt=_prompt(),
        adapter=adapter,
        llm=llm,
        run_id="r",
        max_turns=2,
        log_path=log_path,
    )

    # Second call to LLM should include the bot's first reply in history.
    second_messages = llm.calls[1]["messages"]
    roles_and_content = [(m["role"], m["content"]) for m in second_messages]
    assert ("assistant", "첫발화") in roles_and_content
    assert ("user", "봇1") in roles_and_content


def test_make_session_id_format() -> None:
    assert make_session_id("run-abc", "persona-1") == "eval-run-abc-persona-1"


@pytest.mark.parametrize("turns", [1, 5, 10])
async def test_run_conversation_respects_max_turns(tmp_path: Path, turns: int) -> None:
    llm = StubLLM(["msg"] * turns)
    adapter = MockChatbotAdapter()
    log_path = tmp_path / f"conv-{turns}.jsonl"
    result = await run_conversation(
        persona=_persona(),
        persona_prompt=_prompt(),
        adapter=adapter,
        llm=llm,
        run_id="r",
        max_turns=turns,
        log_path=log_path,
    )
    assert result.total_user_turns == turns
    assert result.total_bot_turns == turns
