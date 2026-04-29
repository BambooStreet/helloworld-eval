import asyncio
import json
import statistics
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..core.llm import LLMClient
from ..core.prompts import Prompt
from ..core.settings import Settings
from ..personas.schema import Persona
from ..simulator.orchestrator import ConversationResult, TurnLog

DIMENSIONS: list[str] = [
    "task_completion",
    "factual_correctness",
    "intent_understanding",
    "consistency",
    "safety",
    "efficiency",
]

JUDGE_PROMPT_VERSION = "v1"


def prompt_id_for(dimension: str) -> str:
    return f"judge_{dimension}"


class JudgeScore(BaseModel):
    score: int = Field(ge=1, le=5)
    rationale: str


class DimensionResult(BaseModel):
    dimension: str
    scores: list[JudgeScore]
    median: float
    mean: float
    stddev: float


class ConversationEvaluation(BaseModel):
    run_id: str
    persona_id: str
    seed_id: str
    n_repetitions: int
    transcript_turn_count: int
    dimensions: dict[str, DimensionResult] = Field(default_factory=dict)


def render_transcript(turns: list[TurnLog]) -> str:
    """Render turns as a readable bilingual transcript fed to the judge."""
    lines: list[str] = []
    for t in turns:
        role_label = "사용자" if t.role == "user" else "챗봇"
        lines.append(f"[T{t.turn_idx}] {role_label}: {t.content}")
    return "\n".join(lines)


def render_persona_brief(persona: Persona) -> str:
    payload: dict[str, Any] = {
        "id": persona.id,
        "name": persona.name,
        "task": persona.task.model_dump(),
        "background_story": persona.background_story,
        "communication_style": persona.communication_style.model_dump(),
        "demographics": {
            "nationality": persona.demographics.nationality,
            "korean_proficiency": persona.demographics.korean_proficiency,
            "occupation": persona.demographics.occupation,
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def load_judge_prompts(prompts_dir: Path) -> dict[str, Prompt]:
    return {
        dim: Prompt.load(prompts_dir, prompt_id_for(dim), JUDGE_PROMPT_VERSION)
        for dim in DIMENSIONS
    }


async def call_judge_once(
    client: LLMClient,
    prompt: Prompt,
    persona: Persona,
    transcript: str,
) -> JudgeScore:
    user_msg = (
        f"## 페르소나\n```json\n{render_persona_brief(persona)}\n```\n\n"
        f"## 대화 전체 Transcript\n{transcript}\n"
    )
    result = await client.chat(
        role="judge",
        messages=[
            {"role": "system", "content": prompt.body},
            {"role": "user", "content": user_msg},
        ],
        prompt=prompt,
        temperature=0.0,
        json_mode=True,
    )
    try:
        data = json.loads(result.content)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Judge {prompt.id} returned invalid JSON: {exc}\n"
            f"Content (first 300 chars): {result.content[:300]}"
        ) from exc
    return JudgeScore.model_validate(data)


async def evaluate_dimension(
    client: LLMClient,
    prompt: Prompt,
    dimension: str,
    persona: Persona,
    transcript: str,
    n: int,
) -> DimensionResult:
    scores = await asyncio.gather(
        *(call_judge_once(client, prompt, persona, transcript) for _ in range(n))
    )
    score_values = [s.score for s in scores]
    return DimensionResult(
        dimension=dimension,
        scores=list(scores),
        median=float(statistics.median(score_values)),
        mean=float(statistics.mean(score_values)),
        stddev=float(statistics.stdev(score_values)) if n > 1 else 0.0,
    )


async def evaluate_conversation(
    client: LLMClient,
    prompts: dict[str, Prompt],
    persona: Persona,
    conversation: ConversationResult,
    n_repetitions: int = 3,
) -> ConversationEvaluation:
    transcript = render_transcript(conversation.turns)
    dimension_tasks = [
        evaluate_dimension(client, prompts[dim], dim, persona, transcript, n_repetitions)
        for dim in DIMENSIONS
    ]
    results = await asyncio.gather(*dimension_tasks)
    return ConversationEvaluation(
        run_id=conversation.run_id,
        persona_id=persona.id,
        seed_id=persona.seed_id,
        n_repetitions=n_repetitions,
        transcript_turn_count=len(conversation.turns),
        dimensions={r.dimension: r for r in results},
    )


def load_conversation_summary(path: Path) -> ConversationResult:
    """Load the conversation summary JSON written by the simulator (the
    runs/<run_id>/conversation_<seed>.json file)."""
    return ConversationResult.model_validate_json(path.read_text(encoding="utf-8"))


def load_persona(path: Path) -> Persona:
    return Persona.model_validate_json(path.read_text(encoding="utf-8"))


def discover_conversation_summaries(runs_dir: Path) -> list[Path]:
    """Find all runs/*/conversation_*.json files (one per simulated conversation)."""
    return sorted(runs_dir.glob("*/conversation_*.json"))


def default_settings_settings(settings: Settings) -> Path:
    return settings.runs_dir
