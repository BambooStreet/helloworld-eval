"""Top-level orchestration for the E2E pipeline.

Each stage (persona generation, simulation, evaluation) creates its own RunRecorder
so the manifests stay granular. The runner just sequences and applies bounded
concurrency to the simulation stage."""

import asyncio
import time
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .core.llm import LLMClient
from .core.manifest import RunRecorder
from .core.prompts import Prompt
from .core.settings import Settings
from .evaluator.judges import (
    DIMENSIONS,
    ConversationEvaluation,
    discover_conversation_summaries,
    evaluate_conversation,
    load_conversation_summary,
    load_judge_prompts,
    load_persona,
)
from .evaluator.reporter import render_report
from .personas.generator import generate_all_async
from .personas.schema import Persona
from .simulator.adapter import HttpChatbotAdapter
from .simulator.orchestrator import (
    PERSONA_PROMPT_ID,
    PERSONA_PROMPT_VERSION,
    ConversationResult,
    run_conversation,
)


class E2EConfig(BaseModel):
    seed_dir: Path = Path("evaluation/personas/seeds")
    personas_dir: Path = Path("data/personas")
    conversations_dir: Path = Path("data/conversations")
    reports_dir: Path = Path("data/reports")
    adapter_config: Path = Path("configs/chatbot_adapter.yaml")
    n_runs_per_persona: int = 5
    max_turns: int = 15
    sim_concurrency: int = 3
    n_judge_repetitions: int = 3
    adversarial_good: Path | None = None
    adversarial_bad: Path | None = None

    @classmethod
    def from_yaml(cls, path: Path) -> "E2EConfig":
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls.model_validate(data)


class StageResult(BaseModel):
    name: str
    cost_usd: float = 0.0
    duration_s: float = 0.0
    artifacts: list[str] = Field(default_factory=list)
    notes: str | None = None


async def _simulate_one(
    *,
    persona: Persona,
    settings: Settings,
    persona_prompt: Prompt,
    adapter_config: Path,
    max_turns: int,
    output_dir: Path,
    runs_dir: Path,
) -> ConversationResult:
    recorder = RunRecorder(
        runs_dir=runs_dir,
        config={
            "command": "run.simulate",
            "persona_id": persona.id,
            "adapter_config": str(adapter_config),
            "max_turns": max_turns,
        },
        project_root=settings.project_root,
    )
    adapter = HttpChatbotAdapter.from_yaml(adapter_config, settings)
    llm = LLMClient(settings=settings, recorder=recorder)
    log_path = output_dir / f"{recorder.run_id}_{persona.seed_id}.jsonl"
    try:
        result = await run_conversation(
            persona=persona,
            persona_prompt=persona_prompt,
            adapter=adapter,
            llm=llm,
            run_id=recorder.run_id,
            max_turns=max_turns,
            log_path=log_path,
        )
    finally:
        await adapter.aclose()

    result_path = recorder.run_dir / f"conversation_{persona.seed_id}.json"
    result_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    recorder.finalize()
    return result


async def batch_simulate(
    *,
    settings: Settings,
    personas: list[Persona],
    persona_prompt: Prompt,
    adapter_config: Path,
    n_per_persona: int,
    max_turns: int,
    concurrency: int,
    output_dir: Path,
    runs_dir: Path,
) -> list[ConversationResult]:
    semaphore = asyncio.Semaphore(concurrency)

    async def _bounded(persona: Persona) -> ConversationResult:
        async with semaphore:
            return await _simulate_one(
                persona=persona,
                settings=settings,
                persona_prompt=persona_prompt,
                adapter_config=adapter_config,
                max_turns=max_turns,
                output_dir=output_dir,
                runs_dir=runs_dir,
            )

    tasks = [_bounded(p) for p in personas for _ in range(n_per_persona)]
    return await asyncio.gather(*tasks)


async def evaluate_all(
    *,
    settings: Settings,
    config: E2EConfig,
    runs_dir: Path,
) -> tuple[Path, RunRecorder, list[ConversationEvaluation]]:
    persona_by_seed: dict[str, Persona] = {}
    for f in sorted(config.personas_dir.glob("*.json")):
        p = load_persona(f)
        persona_by_seed[p.seed_id] = p

    main_pairs: list[tuple[Persona, ConversationResult]] = []
    for sp in discover_conversation_summaries(runs_dir):
        conv = load_conversation_summary(sp)
        if conv.seed_id in persona_by_seed and conv.turns:
            main_pairs.append((persona_by_seed[conv.seed_id], conv))

    good_pairs: list[tuple[Persona, ConversationResult]] = []
    bad_pairs: list[tuple[Persona, ConversationResult]] = []
    if config.adversarial_good or config.adversarial_bad:
        adv_persona = next(iter(persona_by_seed.values()))
        if config.adversarial_good:
            for path in sorted(config.adversarial_good.glob("*.json")):
                good_pairs.append((adv_persona, load_conversation_summary(path)))
        if config.adversarial_bad:
            for path in sorted(config.adversarial_bad.glob("*.json")):
                bad_pairs.append((adv_persona, load_conversation_summary(path)))

    prompts = load_judge_prompts(settings.prompts_dir)
    recorder = RunRecorder(
        runs_dir=runs_dir,
        config={
            "command": "run.evaluate",
            "n_main": len(main_pairs),
            "n_good": len(good_pairs),
            "n_bad": len(bad_pairs),
            "n_repetitions": config.n_judge_repetitions,
        },
        project_root=settings.project_root,
    )
    llm = LLMClient(settings=settings, recorder=recorder)

    start = time.perf_counter()
    main_tasks = [
        evaluate_conversation(llm, prompts, p, c, n_repetitions=config.n_judge_repetitions)
        for p, c in main_pairs
    ]
    good_tasks = [
        evaluate_conversation(llm, prompts, p, c, n_repetitions=config.n_judge_repetitions)
        for p, c in good_pairs
    ]
    bad_tasks = [
        evaluate_conversation(llm, prompts, p, c, n_repetitions=config.n_judge_repetitions)
        for p, c in bad_pairs
    ]
    all_results = await asyncio.gather(*main_tasks, *good_tasks, *bad_tasks)
    n_main = len(main_tasks)
    n_good = len(good_tasks)
    main_evals = list(all_results[:n_main])
    good_evals = list(all_results[n_main : n_main + n_good])
    bad_evals = list(all_results[n_main + n_good :])
    duration_s = time.perf_counter() - start

    eval_dir = recorder.run_dir / "evaluations"
    eval_dir.mkdir(parents=True, exist_ok=True)
    for ev in main_evals + good_evals + bad_evals:
        (eval_dir / f"{ev.run_id}_{ev.seed_id}.json").write_text(
            ev.model_dump_json(indent=2), encoding="utf-8"
        )

    report_md = render_report(
        main_evals,
        run_id=recorder.run_id,
        total_cost_usd=recorder.manifest.total_cost_usd,
        total_tokens=recorder.manifest.total_tokens,
        duration_seconds=duration_s,
        n_repetitions=config.n_judge_repetitions,
        good_evaluations=good_evals or None,
        bad_evaluations=bad_evals or None,
    )
    report_path = config.reports_dir / f"eval_{recorder.run_id}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_md, encoding="utf-8")
    recorder.finalize()

    return report_path, recorder, main_evals


async def run_e2e(
    *,
    settings: Settings,
    config: E2EConfig,
    skip_personas: bool = False,
    skip_simulation: bool = False,
    skip_evaluation: bool = False,
) -> dict[str, StageResult]:
    """Run the three pipeline stages in sequence. Returns per-stage StageResults."""
    stages: dict[str, StageResult] = {}

    config.personas_dir.mkdir(parents=True, exist_ok=True)
    config.conversations_dir.mkdir(parents=True, exist_ok=True)
    settings.runs_dir.mkdir(parents=True, exist_ok=True)

    # Stage 1: persona generation
    if not skip_personas:
        rec = RunRecorder(
            runs_dir=settings.runs_dir,
            config={"command": "run.personas", "seed_dir": str(config.seed_dir)},
            project_root=settings.project_root,
        )
        start = time.perf_counter()
        paths = await generate_all_async(
            settings=settings,
            recorder=rec,
            seed_dir=config.seed_dir,
            output_dir=config.personas_dir,
        )
        rec.finalize()
        stages["personas"] = StageResult(
            name="personas",
            cost_usd=rec.manifest.total_cost_usd,
            duration_s=time.perf_counter() - start,
            artifacts=[str(p) for p in paths],
        )
    else:
        stages["personas"] = StageResult(name="personas", notes="skipped")

    # Stage 2: simulation
    if not skip_simulation:
        persona_files = sorted(config.personas_dir.glob("*.json"))
        if not persona_files:
            raise RuntimeError(
                f"No personas found in {config.personas_dir}; cannot simulate"
            )
        personas = [load_persona(f) for f in persona_files]
        persona_prompt = Prompt.load(
            settings.prompts_dir, PERSONA_PROMPT_ID, PERSONA_PROMPT_VERSION
        )
        start = time.perf_counter()
        results = await batch_simulate(
            settings=settings,
            personas=personas,
            persona_prompt=persona_prompt,
            adapter_config=config.adapter_config,
            n_per_persona=config.n_runs_per_persona,
            max_turns=config.max_turns,
            concurrency=config.sim_concurrency,
            output_dir=config.conversations_dir,
            runs_dir=settings.runs_dir,
        )
        completed = sum(1 for r in results if r.status == "completed")
        stages["simulation"] = StageResult(
            name="simulation",
            duration_s=time.perf_counter() - start,
            artifacts=[r.run_id for r in results],
            notes=f"{completed}/{len(results)} completed",
        )
    else:
        stages["simulation"] = StageResult(name="simulation", notes="skipped")

    # Stage 3: evaluation
    if not skip_evaluation:
        start = time.perf_counter()
        report_path, eval_rec, main_evals = await evaluate_all(
            settings=settings, config=config, runs_dir=settings.runs_dir
        )
        stages["evaluation"] = StageResult(
            name="evaluation",
            cost_usd=eval_rec.manifest.total_cost_usd,
            duration_s=time.perf_counter() - start,
            artifacts=[str(report_path)],
            notes=f"{len(main_evals)} conversations, {len(DIMENSIONS)} dimensions",
        )
    else:
        stages["evaluation"] = StageResult(name="evaluation", notes="skipped")

    return stages
