from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from ..core.llm import LLMResult
from ..core.models import LLMRole
from ..core.prompts import Prompt
from ..personas.schema import Persona
from .adapter import ChatbotAdapter
from .persona_chat import render_persona_system_prompt

PERSONA_PROMPT_ID = "persona_user"
PERSONA_PROMPT_VERSION = "v1"

ConversationStatus = Literal["completed", "max_turns", "failed"]


class TurnLog(BaseModel):
    turn_idx: int
    role: Literal["user", "bot"]
    content: str
    timestamp: datetime
    latency_ms: float | None = None
    tokens: dict[str, int] | None = None


class ConversationResult(BaseModel):
    persona_id: str
    seed_id: str
    session_id: str
    run_id: str
    status: ConversationStatus
    failure_reason: str | None = None
    total_user_turns: int = 0
    total_bot_turns: int = 0
    log_path: str
    turns: list[TurnLog] = Field(default_factory=list)


class PersonaLLM(Protocol):
    """Subset of LLMClient used by the orchestrator. The signature mirrors
    LLMClient.chat so concrete clients satisfy this protocol structurally.
    Tests inject a StubLLM with **kwargs that also satisfies it."""

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
    ) -> LLMResult: ...


def make_session_id(run_id: str, persona_seed_id: str) -> str:
    return f"eval-{run_id}-{persona_seed_id}"


async def run_conversation(
    *,
    persona: Persona,
    persona_prompt: Prompt,
    adapter: ChatbotAdapter,
    llm: PersonaLLM,
    run_id: str,
    max_turns: int,
    log_path: Path,
    session_id: str | None = None,
    persona_temperature: float = 0.0,
) -> ConversationResult:
    session = session_id or make_session_id(run_id, persona.seed_id)
    system_prompt = render_persona_system_prompt(persona_prompt.body, persona)
    history: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "대화를 시작하세요."},
    ]
    turns: list[TurnLog] = []
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("w", encoding="utf-8") as log_f:
        for t in range(max_turns):
            try:
                persona_result = await llm.chat(
                    role="persona",
                    messages=history,
                    prompt=persona_prompt,
                    temperature=persona_temperature,
                )
            except Exception as exc:
                return _finalize(
                    persona, session, run_id, turns, log_path,
                    "failed", f"persona LLM error: {exc}",
                )

            user_msg = persona_result.content.strip()
            if not user_msg:
                return _finalize(
                    persona, session, run_id, turns, log_path,
                    "failed", "persona returned empty utterance",
                )

            user_turn = TurnLog(
                turn_idx=t,
                role="user",
                content=user_msg,
                timestamp=datetime.now(UTC),
                latency_ms=persona_result.record.latency_ms,
                tokens={
                    "prompt": persona_result.record.usage.prompt_tokens,
                    "completion": persona_result.record.usage.completion_tokens,
                    "total": persona_result.record.usage.total_tokens,
                },
            )
            turns.append(user_turn)
            log_f.write(user_turn.model_dump_json() + "\n")
            log_f.flush()
            history.append({"role": "assistant", "content": user_msg})

            try:
                bot_response = await adapter.send(
                    session_id=session, user_message=user_msg
                )
            except Exception as exc:
                return _finalize(
                    persona, session, run_id, turns, log_path,
                    "failed", f"chatbot error: {exc}",
                )

            bot_turn = TurnLog(
                turn_idx=t,
                role="bot",
                content=bot_response.content,
                timestamp=datetime.now(UTC),
                latency_ms=bot_response.latency_ms,
                tokens=None,
            )
            turns.append(bot_turn)
            log_f.write(bot_turn.model_dump_json() + "\n")
            log_f.flush()
            history.append({"role": "user", "content": bot_response.content})

    return _finalize(persona, session, run_id, turns, log_path, "max_turns", None)


def _finalize(
    persona: Persona,
    session_id: str,
    run_id: str,
    turns: list[TurnLog],
    log_path: Path,
    status: ConversationStatus,
    reason: str | None,
) -> ConversationResult:
    return ConversationResult(
        persona_id=persona.id,
        seed_id=persona.seed_id,
        session_id=session_id,
        run_id=run_id,
        status=status,
        failure_reason=reason,
        total_user_turns=sum(1 for t in turns if t.role == "user"),
        total_bot_turns=sum(1 for t in turns if t.role == "bot"),
        log_path=str(log_path),
        turns=turns,
    )
