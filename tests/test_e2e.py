"""End-to-end regression: persona generation -> simulation -> evaluation -> report,
all using a stub LLM and the in-memory MockChatbotAdapter so no network calls or
API keys are needed. Designed to run in < 3 minutes per M5 acceptance.

The stub responds based on the prompt id seen in each call:
- persona_generator -> returns a canned valid persona JSON
- persona_user      -> returns canned user utterances (last one ends with <<DONE>>)
- judge_<dim>       -> returns canned 1-5 score JSON
"""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from evaluation.core.llm import LLMResult
from evaluation.core.models import LLMCallRecord, LLMUsage
from evaluation.core.prompts import Prompt
from evaluation.evaluator.judges import (
    DIMENSIONS,
    evaluate_conversation,
    load_conversation_summary,
    load_judge_prompts,
    load_persona,
)
from evaluation.evaluator.reporter import render_report
from evaluation.personas.generator import GENERATOR_PROMPT_ID, generate_persona
from evaluation.personas.schema import Persona
from evaluation.simulator.adapter import MockChatbotAdapter
from evaluation.simulator.orchestrator import (
    DONE_TOKEN,
    PERSONA_PROMPT_ID,
    PERSONA_PROMPT_VERSION,
    run_conversation,
)
from tests.test_persona_schema import _full_persona_dict


class E2EStubLLM:
    """Dispatches canned responses by prompt id. Records call counts for assertions."""

    def __init__(
        self,
        persona_json: dict[str, Any],
        persona_messages: list[str],
        judge_score: int = 4,
    ) -> None:
        self.persona_json = persona_json
        self.persona_messages = persona_messages
        self.judge_score = judge_score
        self.call_counts: dict[str, int] = {}
        self._persona_msg_idx = 0

    def _record(self, prompt_id: str | None) -> None:
        key = prompt_id or "unknown"
        self.call_counts[key] = self.call_counts.get(key, 0) + 1

    async def chat(
        self, *, prompt: Prompt | None = None, **kwargs: Any
    ) -> LLMResult:
        prompt_id = prompt.id if prompt else None
        self._record(prompt_id)

        if prompt_id == GENERATOR_PROMPT_ID:
            content = json.dumps(self.persona_json, ensure_ascii=False)
        elif prompt_id == PERSONA_PROMPT_ID:
            msg = self.persona_messages[self._persona_msg_idx]
            self._persona_msg_idx = min(
                self._persona_msg_idx + 1, len(self.persona_messages) - 1
            )
            content = msg
        elif prompt_id and prompt_id.startswith("judge_"):
            content = json.dumps(
                {"score": self.judge_score, "rationale": f"stub for {prompt_id}"},
                ensure_ascii=False,
            )
        else:
            content = "stub response"

        record = LLMCallRecord(
            timestamp=datetime.now(UTC),
            role=kwargs.get("role", "other"),
            model_id="stub",
            prompt_id=prompt_id,
            prompt_version=prompt.version if prompt else None,
            prompt_sha256=prompt.sha256 if prompt else None,
            temperature=kwargs.get("temperature", 0.0),
            usage=LLMUsage(prompt_tokens=50, completion_tokens=20, total_tokens=70),
            usd_cost=0.0,
            latency_ms=1.0,
            response_format="json_object" if kwargs.get("json_mode") else None,
        )
        return LLMResult(content=content, record=record)


def _persona_json_for_stub() -> dict[str, Any]:
    """The stub persona generator returns this. Strips seed_id/generated_with — those are
    set by the generator after parsing the LLM output."""
    full = _full_persona_dict()
    full.pop("seed_id")
    full.pop("generated_with")
    return full


def _seed_yaml(tmp_dir: Path) -> Path:
    seed_dir = tmp_dir / "seeds"
    seed_dir.mkdir(parents=True)
    seed = seed_dir / "p_test.yaml"
    seed.write_text(
        yaml.safe_dump(
            {"seed_id": "p_test", "name_hint": "테스터", "core_situation": "x"},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return seed_dir


def _copy_real_prompts(tmp_dir: Path) -> Path:
    """Copy the project's real prompt files into tmp so the regression test exercises
    the actual prompt content (any change to a prompt is caught here)."""
    project_root = Path(__file__).resolve().parent.parent
    src = project_root / "prompts"
    dst = tmp_dir / "prompts"
    shutil.copytree(src, dst)
    return dst


async def test_e2e_pipeline_with_stubs(tmp_path: Path) -> None:
    """Full pipeline regression: persona gen -> 1 simulated conversation -> evaluate
    all 6 dimensions × 3 reps -> render report. No network, no API key."""
    prompts_dir = _copy_real_prompts(tmp_path)
    _seed_yaml(tmp_path)

    persona_messages = [
        "안녕하세요, 야근수당에 대해 알고 싶어요.",
        "회사에 물어봐도 답이 없었어요.",
        "보복이 걱정돼요.",
        f"감사합니다, 도움 많이 되었어요. {DONE_TOKEN}",
    ]
    stub = E2EStubLLM(
        persona_json=_persona_json_for_stub(),
        persona_messages=persona_messages,
        judge_score=4,
    )

    # Stage 1: persona generation via stub
    gen_prompt = Prompt.load(prompts_dir, GENERATOR_PROMPT_ID, "v1")
    persona = await generate_persona(stub, gen_prompt, {"seed_id": "p_test"})  # type: ignore[arg-type]
    assert persona.seed_id == "p_test"
    assert stub.call_counts[GENERATOR_PROMPT_ID] == 1

    # Stage 2: simulate one conversation against MockChatbotAdapter
    user_prompt = Prompt.load(prompts_dir, PERSONA_PROMPT_ID, PERSONA_PROMPT_VERSION)
    adapter = MockChatbotAdapter()
    log_path = tmp_path / "conv.jsonl"
    convo = await run_conversation(
        persona=persona,
        persona_prompt=user_prompt,
        adapter=adapter,
        llm=stub,
        run_id="e2e-test",
        max_turns=10,
        log_path=log_path,
    )
    assert convo.status == "completed", f"expected DONE termination, got {convo.status}"
    assert convo.total_user_turns == 4
    assert convo.total_bot_turns == 4
    assert log_path.is_file()

    # Stage 3: evaluate all dimensions × 3 reps
    judge_prompts = load_judge_prompts(prompts_dir)
    assert len(judge_prompts) == len(DIMENSIONS)
    eval_result = await evaluate_conversation(
        stub,  # type: ignore[arg-type]
        judge_prompts,
        persona,
        convo,
        n_repetitions=3,
    )
    assert set(eval_result.dimensions.keys()) == set(DIMENSIONS)
    for dim in DIMENSIONS:
        assert eval_result.dimensions[dim].median == 4.0
    # 6 dimensions × 3 reps = 18 judge calls + 1 persona-gen + 4 persona-utterance turns = 23
    expected_judge_calls = 18
    judge_calls = sum(
        v for k, v in stub.call_counts.items() if k.startswith("judge_")
    )
    assert judge_calls == expected_judge_calls

    # Stage 4: render markdown report
    report = render_report(
        [eval_result],
        run_id="e2e-test",
        total_cost_usd=0.0,
        total_tokens=0,
        duration_seconds=0.1,
        n_repetitions=3,
    )
    assert "Evaluation Report" in report
    assert "Per-dimension aggregates" in report
    assert "task_completion" in report
    assert "p_test" in report


async def test_e2e_pipeline_round_trips_to_disk(tmp_path: Path) -> None:
    """The conversation summary written by the simulator must be re-loadable and
    parseable by the evaluator without losing fields. Catches schema drift."""
    prompts_dir = _copy_real_prompts(tmp_path)
    stub = E2EStubLLM(
        persona_json=_persona_json_for_stub(),
        persona_messages=["안녕하세요.", f"고맙습니다 {DONE_TOKEN}"],
        judge_score=3,
    )

    gen_prompt = Prompt.load(prompts_dir, GENERATOR_PROMPT_ID, "v1")
    persona = await generate_persona(stub, gen_prompt, {"seed_id": "p_test"})  # type: ignore[arg-type]
    persona_path = tmp_path / "persona.json"
    persona_path.write_text(persona.model_dump_json(indent=2), encoding="utf-8")
    Persona.model_validate_json(persona_path.read_text(encoding="utf-8"))

    user_prompt = Prompt.load(prompts_dir, PERSONA_PROMPT_ID, PERSONA_PROMPT_VERSION)
    adapter = MockChatbotAdapter()
    convo = await run_conversation(
        persona=persona,
        persona_prompt=user_prompt,
        adapter=adapter,
        llm=stub,
        run_id="rt-test",
        max_turns=5,
        log_path=tmp_path / "conv.jsonl",
    )
    convo_path = tmp_path / "conversation.json"
    convo_path.write_text(convo.model_dump_json(indent=2), encoding="utf-8")

    reloaded = load_conversation_summary(convo_path)
    assert reloaded.status == convo.status
    assert reloaded.total_user_turns == convo.total_user_turns
    assert len(reloaded.turns) == len(convo.turns)
    reloaded_persona = load_persona(persona_path)
    assert reloaded_persona.seed_id == persona.seed_id


def test_e2e_config_loads_v1_yaml() -> None:
    """The shipped configs/v1.yaml must parse against E2EConfig schema. Catches
    config-schema drift in either direction."""
    from evaluation.runner import E2EConfig

    project_root = Path(__file__).resolve().parent.parent
    config_path = project_root / "configs" / "v1.yaml"
    assert config_path.is_file(), "configs/v1.yaml is required for E2E"
    config = E2EConfig.from_yaml(config_path)
    assert config.n_runs_per_persona >= 1
    assert config.max_turns >= 1
    assert config.n_judge_repetitions >= 1
