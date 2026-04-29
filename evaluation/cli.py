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
from .personas.generator import (  # noqa: E402
    default_output_dir,
    default_seed_dir,
    generate_all_async,
)
from .personas.schema import Persona  # noqa: E402
from .simulator.adapter import (  # noqa: E402
    ChatbotAdapter,
    HttpChatbotAdapter,
    MockChatbotAdapter,
)
from .simulator.orchestrator import (  # noqa: E402
    PERSONA_PROMPT_ID,
    PERSONA_PROMPT_VERSION,
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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
