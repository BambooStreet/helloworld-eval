from dotenv import load_dotenv

# Load .env (if present) before any module reads environment variables.
load_dotenv()

import asyncio  # noqa: E402
from pathlib import Path  # noqa: E402

import typer  # noqa: E402

from .core.manifest import RunRecorder  # noqa: E402
from .core.settings import Settings  # noqa: E402
from .personas.generator import (  # noqa: E402
    default_output_dir,
    default_seed_dir,
    generate_all_async,
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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
