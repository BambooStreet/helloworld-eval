"""Evaluator tests use a stub LLM that returns canned JudgeScores so the
parsing/aggregation/Krippendorff/reporter pipeline runs without spending tokens."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from evaluation.core.llm import LLMResult
from evaluation.core.models import LLMCallRecord, LLMUsage
from evaluation.core.prompts import Prompt
from evaluation.evaluator.judges import (
    DIMENSIONS,
    JudgeScore,
    call_judge_once,
    evaluate_conversation,
    evaluate_dimension,
    render_persona_brief,
    render_transcript,
)
from evaluation.evaluator.reliability import (
    adversarial_score_diff,
    aggregate_summary,
    krippendorff_alpha_per_dimension,
    per_persona_summary,
)
from evaluation.evaluator.reporter import render_report
from evaluation.personas.schema import Persona
from evaluation.simulator.orchestrator import ConversationResult, TurnLog
from tests.test_persona_schema import _full_persona_dict


def _persona() -> Persona:
    return Persona.model_validate(_full_persona_dict())


def _make_turn(
    idx: int, role: str, content: str, latency: float = 100.0
) -> TurnLog:
    return TurnLog(
        turn_idx=idx,
        role=role,
        content=content,
        timestamp=datetime.now(UTC),
        latency_ms=latency,
        tokens={"prompt": 10, "completion": 5, "total": 15} if role == "user" else None,
    )


def _make_conversation(seed_id: str = "p_test", n_turns: int = 3) -> ConversationResult:
    turns: list[TurnLog] = []
    for i in range(n_turns):
        turns.append(_make_turn(i, "user", f"사용자 {i}"))
        turns.append(_make_turn(i, "bot", f"챗봇 {i}"))
    return ConversationResult(
        persona_id=seed_id,
        seed_id=seed_id,
        session_id=f"sess-{seed_id}",
        run_id=f"run-{seed_id}",
        status="completed",
        failure_reason=None,
        total_user_turns=n_turns,
        total_bot_turns=n_turns,
        log_path="synthetic",
        turns=turns,
    )


class StubJudgeLLM:
    """Returns canned scores. `score_plan` maps prompt_id -> list of (score, rationale)
    cycled per call. Default 3 for any unknown prompt."""

    def __init__(self, score_plan: dict[str, list[tuple[int, str]]] | None = None) -> None:
        self.score_plan = score_plan or {}
        self._counters: dict[str, int] = {}

    async def chat(self, **kwargs: Any) -> LLMResult:
        prompt: Prompt = kwargs["prompt"]
        plan = self.score_plan.get(prompt.id)
        if plan is None:
            score, rationale = 3, f"default rationale for {prompt.id}"
        else:
            idx = self._counters.get(prompt.id, 0) % len(plan)
            self._counters[prompt.id] = self._counters.get(prompt.id, 0) + 1
            score, rationale = plan[idx]
        record = LLMCallRecord(
            timestamp=datetime.now(UTC),
            role="judge",
            model_id="stub",
            prompt_id=prompt.id,
            prompt_version=prompt.version,
            prompt_sha256=prompt.sha256,
            temperature=0.0,
            usage=LLMUsage(prompt_tokens=100, completion_tokens=20, total_tokens=120),
            usd_cost=0.0,
            latency_ms=1.0,
            response_format="json_object",
        )
        content = json.dumps({"score": score, "rationale": rationale}, ensure_ascii=False)
        return LLMResult(content=content, record=record)


def _make_prompt(prompt_id: str) -> Prompt:
    return Prompt(id=prompt_id, version="v1", body="...", sha256="a" * 64)


def _all_prompts() -> dict[str, Prompt]:
    return {dim: _make_prompt(f"judge_{dim}") for dim in DIMENSIONS}


def test_render_transcript_format() -> None:
    conv = _make_conversation(n_turns=2)
    out = render_transcript(conv.turns)
    assert "[T0] 사용자: 사용자 0" in out
    assert "[T0] 챗봇: 챗봇 0" in out
    assert "[T1] 사용자: 사용자 1" in out


def test_render_persona_brief_includes_task_and_communication_style() -> None:
    out = render_persona_brief(_persona())
    parsed = json.loads(out)
    assert parsed["id"] == "p_test"
    assert "task" in parsed
    assert "success_criteria" in parsed["task"]
    assert "communication_style" in parsed
    assert parsed["demographics"]["nationality"] == "베트남"


def test_judge_score_validates_range() -> None:
    from pydantic import ValidationError

    JudgeScore(score=1, rationale="ok")
    JudgeScore(score=5, rationale="ok")
    with pytest.raises(ValidationError):
        JudgeScore(score=0, rationale="ok")
    with pytest.raises(ValidationError):
        JudgeScore(score=6, rationale="ok")


async def test_call_judge_once_parses_canned_response() -> None:
    llm = StubJudgeLLM(
        score_plan={"judge_task_completion": [(4, "전반적으로 충족됨")]}
    )
    result = await call_judge_once(
        llm,  # type: ignore[arg-type]
        _make_prompt("judge_task_completion"),
        _persona(),
        "transcript",
    )
    assert result.score == 4
    assert "충족" in result.rationale


async def test_call_judge_once_invalid_json_raises() -> None:
    class BadLLM:
        async def chat(self, **kwargs: Any) -> LLMResult:
            record = LLMCallRecord(
                timestamp=datetime.now(UTC),
                role="judge",
                model_id="stub",
                temperature=0.0,
                usage=LLMUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                usd_cost=0.0,
                latency_ms=1.0,
            )
            return LLMResult(content="not json", record=record)

    with pytest.raises(ValueError, match="invalid JSON"):
        await call_judge_once(
            BadLLM(),  # type: ignore[arg-type]
            _make_prompt("judge_task_completion"),
            _persona(),
            "transcript",
        )


async def test_evaluate_dimension_aggregates_n_calls() -> None:
    llm = StubJudgeLLM(
        score_plan={"judge_task_completion": [(3, "a"), (4, "b"), (5, "c")]}
    )
    result = await evaluate_dimension(
        llm,  # type: ignore[arg-type]
        _make_prompt("judge_task_completion"),
        "task_completion",
        _persona(),
        "transcript",
        n=3,
    )
    assert result.dimension == "task_completion"
    assert len(result.scores) == 3
    assert result.median == 4.0
    assert result.mean == 4.0
    assert result.stddev > 0


async def test_evaluate_conversation_runs_all_dimensions() -> None:
    llm = StubJudgeLLM()  # default 3 for everything
    conv = _make_conversation(n_turns=2)
    result = await evaluate_conversation(
        llm,  # type: ignore[arg-type]
        _all_prompts(),
        _persona(),
        conv,
        n_repetitions=3,
    )
    assert set(result.dimensions.keys()) == set(DIMENSIONS)
    assert all(d.median == 3.0 for d in result.dimensions.values())
    assert result.transcript_turn_count == 4
    assert result.run_id == conv.run_id


def _make_eval(
    seed_id: str,
    scores_per_dim: dict[str, list[int]],
    n_repetitions: int = 3,
) -> Any:
    from evaluation.evaluator.judges import (
        ConversationEvaluation,
        DimensionResult,
    )

    dims = {}
    import statistics as st

    for dim in DIMENSIONS:
        s = scores_per_dim.get(dim, [3] * n_repetitions)
        dims[dim] = DimensionResult(
            dimension=dim,
            scores=[JudgeScore(score=v, rationale="r") for v in s],
            median=float(st.median(s)),
            mean=float(st.mean(s)),
            stddev=float(st.stdev(s)) if len(s) > 1 else 0.0,
        )
    return ConversationEvaluation(
        run_id=f"run-{seed_id}",
        persona_id=seed_id,
        seed_id=seed_id,
        n_repetitions=n_repetitions,
        transcript_turn_count=10,
        dimensions=dims,
    )


def test_krippendorff_alpha_perfect_agreement() -> None:
    """When all 3 raters give identical scores across all items, α should be NaN
    (no observed disagreement, denominator zero) — the function returns NaN gracefully."""
    evals = [
        _make_eval("a", {dim: [4, 4, 4] for dim in DIMENSIONS}),
        _make_eval("b", {dim: [4, 4, 4] for dim in DIMENSIONS}),
    ]
    alphas = krippendorff_alpha_per_dimension(evals)
    assert set(alphas.keys()) == set(DIMENSIONS)
    # All raters identical across all items -> denominator zero -> NaN handled
    assert all(np.isnan(a) for a in alphas.values())


def test_krippendorff_alpha_high_disagreement_negative() -> None:
    """Three raters give very different scores → α near 0 or negative."""
    evals = [
        _make_eval("a", {dim: [1, 3, 5] for dim in DIMENSIONS}),
        _make_eval("b", {dim: [5, 3, 1] for dim in DIMENSIONS}),
    ]
    alphas = krippendorff_alpha_per_dimension(evals)
    for dim in DIMENSIONS:
        assert alphas[dim] < 0.5


def test_adversarial_score_diff_returns_per_dim_diffs() -> None:
    good = [
        _make_eval("g1", {dim: [5, 5, 5] for dim in DIMENSIONS}),
        _make_eval("g2", {dim: [4, 5, 4] for dim in DIMENSIONS}),
    ]
    bad = [
        _make_eval("b1", {dim: [2, 1, 2] for dim in DIMENSIONS}),
        _make_eval("b2", {dim: [1, 2, 1] for dim in DIMENSIONS}),
    ]
    diffs = adversarial_score_diff(good, bad)
    for dim in DIMENSIONS:
        assert diffs[dim]["good_mean"] > diffs[dim]["bad_mean"]
        assert diffs[dim]["diff"] >= 2.0


def test_aggregate_summary_returns_per_dim_stats() -> None:
    evals = [
        _make_eval("a", {dim: [4, 4, 4] for dim in DIMENSIONS}),
        _make_eval("b", {dim: [3, 3, 3] for dim in DIMENSIONS}),
    ]
    out = aggregate_summary(evals)
    for dim in DIMENSIONS:
        assert out[dim]["mean"] == pytest.approx(3.5)
        assert out[dim]["median"] == 3.5


def test_per_persona_summary_groups_by_seed_id() -> None:
    evals = [
        _make_eval("seedA", {dim: [4, 4, 4] for dim in DIMENSIONS}),
        _make_eval("seedA", {dim: [3, 3, 3] for dim in DIMENSIONS}),
        _make_eval("seedB", {dim: [5, 5, 5] for dim in DIMENSIONS}),
    ]
    out = per_persona_summary(evals)
    assert set(out.keys()) == {"seedA", "seedB"}
    assert out["seedA"]["n_conversations"] == 2
    assert out["seedB"]["overall_mean"] == 5.0


def test_render_report_includes_all_sections() -> None:
    evals = [
        _make_eval("a", {dim: [4, 4, 5] for dim in DIMENSIONS}),
        _make_eval("b", {dim: [3, 3, 4] for dim in DIMENSIONS}),
    ]
    good = [_make_eval("g", {dim: [5, 5, 5] for dim in DIMENSIONS})]
    bad = [_make_eval("b", {dim: [1, 1, 2] for dim in DIMENSIONS})]
    md = render_report(
        evals,
        run_id="run-test",
        total_cost_usd=0.123456,
        total_tokens=1234,
        duration_seconds=12.3,
        n_repetitions=3,
        good_evaluations=good,
        bad_evaluations=bad,
    )
    assert "Evaluation Report" in md
    assert "Per-dimension aggregates" in md
    assert "Per-persona aggregates" in md
    assert "Adversarial sanity" in md
    assert "Per-conversation detail" in md
    assert "$0.123456" in md
    assert "task_completion" in md


def test_persona_with_real_data_loads(tmp_path: Path) -> None:
    """Spot check that the actual generated persona JSONs in data/personas (if any)
    parse via Persona.model_validate_json. Only runs if files exist."""
    persona_dir = Path(__file__).resolve().parent.parent / "data" / "personas"
    if not persona_dir.is_dir():
        pytest.skip("data/personas dir not present")
    files = list(persona_dir.glob("*.json"))
    if not files:
        pytest.skip("no persona files generated yet")
    for f in files:
        Persona.model_validate_json(f.read_text(encoding="utf-8"))


def test_adversarial_conversations_load() -> None:
    """The hand-crafted adversarial JSONs must parse as ConversationResult."""
    adv_dir = Path(__file__).resolve().parent.parent / "data" / "adversarial"
    good_files = list((adv_dir / "good").glob("*.json"))
    bad_files = list((adv_dir / "bad").glob("*.json"))
    assert len(good_files) >= 2, "expected at least 2 good adversarial conversations"
    assert len(bad_files) >= 2, "expected at least 2 bad adversarial conversations"
    for f in good_files + bad_files:
        conv = ConversationResult.model_validate_json(f.read_text(encoding="utf-8"))
        assert conv.turns
