from pathlib import Path

import typer

from .core.manifest import RunRecorder
from .core.settings import Settings

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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
