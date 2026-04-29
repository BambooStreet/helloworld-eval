from dotenv import load_dotenv

# Load .env (if present) before any module reads environment variables.
load_dotenv()

import asyncio  # noqa: E402
from pathlib import Path  # noqa: E402

import typer  # noqa: E402

from .core.llm import LLMClient  # noqa: E402
from .core.manifest import RunRecorder  # noqa: E402
from .core.prompts import Prompt  # noqa: E402
from .core.settings import Settings  # noqa: E402
from .evaluator.judges import (  # noqa: E402
    DIMENSIONS,
    ConversationEvaluation,
    discover_conversation_summaries,
    evaluate_conversation,
    load_conversation_summary,
    load_judge_prompts,
    load_persona,
)
from .evaluator.reporter import render_report  # noqa: E402
from .personas.generator import (  # noqa: E402
    default_output_dir,
    default_seed_dir,
    generate_all_async,
)
from .personas.schema import Persona  # noqa: E402
from .runner import E2EConfig, run_e2e  # noqa: E402
from .simulator.adapter import (  # noqa: E402
    ChatbotAdapter,
    HttpChatbotAdapter,
    MockChatbotAdapter,
)
from .simulator.orchestrator import (  # noqa: E402
    PERSONA_PROMPT_ID,
    PERSONA_PROMPT_VERSION,
    ConversationResult,
    run_conversation,
)

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def _root() -> None:
    """페르소나 LLM 기반 챗봇 평가 파이프라인."""


@app.command()
def hello(
    runs_dir: Path | None = typer.Option(None, help="Override runs directory"),
) -> None:
    """Sanity check: create a run manifest with no LLM calls."""
    settings = Settings()
    target = runs_dir or settings.runs_dir
    target.mkdir(parents=True, exist_ok=True)

    recorder = RunRecorder(
        runs_dir=target,
        config={"command": "hello"},
        project_root=settings.project_root,
    )
    path = recorder.finalize()
    typer.echo(f"Created manifest: {path}")
    typer.echo(f"  run_id: {recorder.run_id}")
    typer.echo(f"  llm_calls: {len(recorder.manifest.llm_calls)}")
    typer.echo(f"  total_cost_usd: {recorder.manifest.total_cost_usd}")


@app.command("persona-generate")
def persona_generate(
    seed_dir: Path | None = typer.Option(
        None, help="Override seed directory (default: evaluation/personas/seeds)"
    ),
    output_dir: Path | None = typer.Option(
        None, help="Override output directory (default: data/personas)"
    ),
    runs_dir: Path | None = typer.Option(None, help="Override runs directory"),
) -> None:
    """Expand seed YAMLs into validated persona JSONs via gpt-4o-mini."""
    settings = Settings()
    seeds = seed_dir or default_seed_dir(settings)
    out = output_dir or default_output_dir(settings)
    runs = runs_dir or settings.runs_dir
    runs.mkdir(parents=True, exist_ok=True)

    recorder = RunRecorder(
        runs_dir=runs,
        config={
            "command": "persona-generate",
            "seed_dir": str(seeds),
            "output_dir": str(out),
        },
        project_root=settings.project_root,
    )

    paths = asyncio.run(
        generate_all_async(
            settings=settings,
            recorder=recorder,
            seed_dir=seeds,
            output_dir=out,
        )
    )
    manifest_path = recorder.finalize()

    typer.echo(f"Generated {len(paths)} personas:")
    for p in paths:
        typer.echo(f"  {p}")
    typer.echo(f"Manifest: {manifest_path}")
    typer.echo(f"Total cost: ${recorder.manifest.total_cost_usd:.6f}")


@app.command()
def simulate(
    persona_path: Path = typer.Option(..., "--persona", help="Persona JSON path"),
    adapter_config: Path = typer.Option(
        Path("configs/chatbot_adapter.yaml"),
        "--adapter",
        help="Chatbot adapter YAML config",
    ),
    max_turns: int = typer.Option(50, "--max-turns"),
    use_mock: bool = typer.Option(
        False, "--mock", help="Use deterministic mock chatbot instead of real endpoint"
    ),
    session_id: str | None = typer.Option(
        None, "--session-id", help="Override session_id (default: eval-{run_id}-{seed_id})"
    ),
    output_dir: Path | None = typer.Option(
        None, "--output-dir", help="Conversation log output dir (default: data/conversations)"
    ),
    runs_dir: Path | None = typer.Option(None, "--runs-dir"),
) -> None:
    """Run a simulated multi-turn conversation between persona and chatbot."""
    settings = Settings()
    runs_target = runs_dir or settings.runs_dir
    runs_target.mkdir(parents=True, exist_ok=True)
    output_target = output_dir or (settings.data_dir / "conversations")
    output_target.mkdir(parents=True, exist_ok=True)

    persona = Persona.model_validate_json(persona_path.read_text(encoding="utf-8"))
    persona_prompt = Prompt.load(
        settings.prompts_dir, PERSONA_PROMPT_ID, PERSONA_PROMPT_VERSION
    )

    recorder = RunRecorder(
        runs_dir=runs_target,
        config={
            "command": "simulate",
            "persona_path": str(persona_path),
            "adapter_config": None if use_mock else str(adapter_config),
            "max_turns": max_turns,
            "mock": use_mock,
            "session_id_override": session_id,
        },
        project_root=settings.project_root,
    )
    log_path = output_target / f"{recorder.run_id}_{persona.seed_id}.jsonl"

    async def _run() -> None:
        adapter: ChatbotAdapter
        if use_mock:
            adapter = MockChatbotAdapter()
        else:
            adapter = HttpChatbotAdapter.from_yaml(adapter_config, settings)
        llm = LLMClient(settings=settings, recorder=recorder)
        try:
            result = await run_conversation(
                persona=persona,
                persona_prompt=persona_prompt,
                adapter=adapter,
                llm=llm,
                run_id=recorder.run_id,
                max_turns=max_turns,
                log_path=log_path,
                session_id=session_id,
            )
        finally:
            await adapter.aclose()
        result_path = recorder.run_dir / f"conversation_{persona.seed_id}.json"
        result_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        suffix = f" ({result.failure_reason})" if result.failure_reason else ""
        typer.echo(f"Status: {result.status}{suffix}")
        typer.echo(f"Turns: user={result.total_user_turns}, bot={result.total_bot_turns}")
        typer.echo(f"Conversation log: {log_path}")
        typer.echo(f"Result: {result_path}")

    asyncio.run(_run())
    manifest_path = recorder.finalize()
    typer.echo(f"Manifest: {manifest_path}")
    typer.echo(f"Cost: ${recorder.manifest.total_cost_usd:.6f}")


@app.command()
def evaluate(
    runs_scan_dir: Path | None = typer.Option(
        None,
        "--runs-scan",
        help="Scan this dir for runs/<run_id>/conversation_*.json (default: runs/)",
    ),
    personas_dir: Path | None = typer.Option(
        None, "--personas-dir", help="Persona JSON dir (default: data/personas)"
    ),
    output: Path | None = typer.Option(
        None, "--output", help="Markdown report path (default: data/reports/eval_{run_id}.md)"
    ),
    n_repetitions: int = typer.Option(3, "--n", help="Judge call repetitions per dimension"),
    runs_dir: Path | None = typer.Option(None, "--runs-dir", help="Manifest output dir"),
    adversarial_good: Path | None = typer.Option(
        None,
        "--adv-good",
        help="Dir containing good_*.json adversarial conversation summaries",
    ),
    adversarial_bad: Path | None = typer.Option(
        None,
        "--adv-bad",
        help="Dir containing bad_*.json adversarial conversation summaries",
    ),
    adversarial_persona: Path | None = typer.Option(
        None,
        "--adv-persona",
        help="Persona JSON used for adversarial scoring (defaults to first persona found)",
    ),
) -> None:
    """Evaluate all simulated conversations across 6 dimensions and write a Markdown report."""
    settings = Settings()
    runs_target = runs_dir or settings.runs_dir
    runs_target.mkdir(parents=True, exist_ok=True)
    personas_target = personas_dir or (settings.data_dir / "personas")
    scan_dir = runs_scan_dir or settings.runs_dir

    persona_by_seed: dict[str, Persona] = {}
    for persona_file in sorted(personas_target.glob("*.json")):
        persona = load_persona(persona_file)
        persona_by_seed[persona.seed_id] = persona
    if not persona_by_seed:
        raise typer.BadParameter(f"No persona JSONs found in {personas_target}")

    summary_paths = discover_conversation_summaries(scan_dir)
    conversations: list[tuple[Persona, ConversationResult]] = []
    for sp in summary_paths:
        conv = load_conversation_summary(sp)
        if conv.seed_id not in persona_by_seed:
            typer.echo(f"  skipping {sp}: persona for seed_id {conv.seed_id} not found")
            continue
        if not conv.turns:
            typer.echo(f"  skipping {sp}: no turns")
            continue
        conversations.append((persona_by_seed[conv.seed_id], conv))

    if not conversations:
        raise typer.BadParameter(
            f"No usable conversation summaries found under {scan_dir}"
        )

    typer.echo(f"Loaded {len(conversations)} conversations and {len(persona_by_seed)} personas")

    adv_good_convs: list[tuple[Persona, ConversationResult]] = []
    adv_bad_convs: list[tuple[Persona, ConversationResult]] = []
    if adversarial_good or adversarial_bad:
        adv_persona = (
            load_persona(adversarial_persona)
            if adversarial_persona
            else next(iter(persona_by_seed.values()))
        )
        if adversarial_good:
            for path in sorted(adversarial_good.glob("*.json")):
                adv_good_convs.append((adv_persona, load_conversation_summary(path)))
        if adversarial_bad:
            for path in sorted(adversarial_bad.glob("*.json")):
                adv_bad_convs.append((adv_persona, load_conversation_summary(path)))
        typer.echo(
            f"Adversarial: {len(adv_good_convs)} good, {len(adv_bad_convs)} bad"
        )

    prompts = load_judge_prompts(settings.prompts_dir)
    recorder = RunRecorder(
        runs_dir=runs_target,
        config={
            "command": "evaluate",
            "n_conversations": len(conversations),
            "n_repetitions": n_repetitions,
            "n_adv_good": len(adv_good_convs),
            "n_adv_bad": len(adv_bad_convs),
        },
        project_root=settings.project_root,
    )
    llm = LLMClient(settings=settings, recorder=recorder)

    async def _evaluate_all() -> tuple[
        list[ConversationEvaluation],
        list[ConversationEvaluation],
        list[ConversationEvaluation],
    ]:
        main_tasks = [
            evaluate_conversation(llm, prompts, p, c, n_repetitions=n_repetitions)
            for p, c in conversations
        ]
        good_tasks = [
            evaluate_conversation(llm, prompts, p, c, n_repetitions=n_repetitions)
            for p, c in adv_good_convs
        ]
        bad_tasks = [
            evaluate_conversation(llm, prompts, p, c, n_repetitions=n_repetitions)
            for p, c in adv_bad_convs
        ]
        all_results = await asyncio.gather(*main_tasks, *good_tasks, *bad_tasks)
        n_main = len(main_tasks)
        n_good = len(good_tasks)
        return (
            list(all_results[:n_main]),
            list(all_results[n_main : n_main + n_good]),
            list(all_results[n_main + n_good :]),
        )

    import time as _time

    start = _time.perf_counter()
    main_evals, good_evals, bad_evals = asyncio.run(_evaluate_all())
    duration_s = _time.perf_counter() - start

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
        n_repetitions=n_repetitions,
        good_evaluations=good_evals or None,
        bad_evaluations=bad_evals or None,
    )
    report_path = output or (settings.data_dir / "reports" / f"eval_{recorder.run_id}.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_md, encoding="utf-8")
    manifest_path = recorder.finalize()

    typer.echo(f"Report: {report_path}")
    typer.echo(f"Manifest: {manifest_path}")
    typer.echo(f"Per-conversation evals: {eval_dir}")
    typer.echo(f"Cost: ${recorder.manifest.total_cost_usd:.6f}")
    typer.echo(f"Duration: {duration_s:.1f}s")
    typer.echo(f"Dimensions evaluated: {len(DIMENSIONS)}")


@app.command()
def run(
    config_path: Path = typer.Option(
        Path("configs/v1.yaml"), "--config", help="E2E config YAML"
    ),
    skip_personas: bool = typer.Option(False, "--skip-personas"),
    skip_simulation: bool = typer.Option(False, "--skip-simulation"),
    skip_evaluation: bool = typer.Option(False, "--skip-evaluation"),
) -> None:
    """Full E2E pipeline: persona generation -> N simulations per persona -> evaluation.

    Each stage produces its own run manifest under runs/. Use --skip-* to reuse
    artifacts from a previous partial run.
    """
    config = E2EConfig.from_yaml(config_path)
    settings = Settings()

    typer.echo(f"=== E2E pipeline ({config_path}) ===")
    typer.echo(
        f"  personas: skip={skip_personas}, sim: skip={skip_simulation}, "
        f"eval: skip={skip_evaluation}"
    )
    typer.echo(
        f"  config: n_runs/persona={config.n_runs_per_persona}, "
        f"max_turns={config.max_turns}, sim_concurrency={config.sim_concurrency}, "
        f"n_judge_reps={config.n_judge_repetitions}"
    )
    typer.echo("")

    stages = asyncio.run(
        run_e2e(
            settings=settings,
            config=config,
            skip_personas=skip_personas,
            skip_simulation=skip_simulation,
            skip_evaluation=skip_evaluation,
        )
    )

    total_cost = 0.0
    for name, stage in stages.items():
        typer.echo(f"--- {name} ---")
        typer.echo(f"  duration: {stage.duration_s:.1f}s")
        typer.echo(f"  cost:     ${stage.cost_usd:.6f}")
        if stage.notes:
            typer.echo(f"  notes:    {stage.notes}")
        if stage.artifacts:
            preview = stage.artifacts[:3]
            more = f" (+{len(stage.artifacts) - 3} more)" if len(stage.artifacts) > 3 else ""
            typer.echo(f"  artifacts: {preview}{more}")
        total_cost += stage.cost_usd

    typer.echo("")
    typer.echo(f"=== Total LLM cost: ${total_cost:.6f} ===")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
