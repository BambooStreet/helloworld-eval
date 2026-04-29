from pathlib import Path

import yaml
from typer.testing import CliRunner

from evaluation.cli import app


def test_hello_creates_manifest(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["hello", "--runs-dir", str(tmp_path)])
    assert result.exit_code == 0, result.stdout

    runs = list(tmp_path.iterdir())
    assert len(runs) == 1
    manifest_path = runs[0] / "manifest.yaml"
    assert manifest_path.is_file()

    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert data["config"] == {"command": "hello"}
    assert data["llm_calls"] == []
    assert data["totals"]["llm_calls"] == 0
