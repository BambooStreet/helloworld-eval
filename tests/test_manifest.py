from datetime import UTC, datetime
from pathlib import Path

import yaml

from evaluation.core.manifest import RunRecorder, new_run_id
from evaluation.core.models import LLMCallRecord, LLMUsage


def test_run_id_is_unique() -> None:
    assert new_run_id() != new_run_id()


def test_recorder_creates_run_dir(tmp_path: Path) -> None:
    rec = RunRecorder(runs_dir=tmp_path, config={"k": "v"})
    assert rec.run_dir.is_dir()
    assert rec.run_dir.parent == tmp_path
    assert rec.manifest_path == rec.run_dir / "manifest.yaml"


def test_recorder_finalize_writes_yaml(tmp_path: Path) -> None:
    rec = RunRecorder(runs_dir=tmp_path, config={"command": "hello"})
    rec.add_llm_call(
        LLMCallRecord(
            timestamp=datetime.now(UTC),
            role="other",
            model_id="gpt-4o-mini",
            prompt_id="p",
            prompt_version="v1",
            prompt_sha256="x" * 64,
            temperature=0.0,
            usage=LLMUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            usd_cost=10 * 0.150 / 1e6 + 5 * 0.600 / 1e6,
            latency_ms=123.4,
        )
    )
    path = rec.finalize()

    assert path.is_file()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert data["run_id"] == rec.run_id
    assert data["config"] == {"command": "hello"}
    assert data["python_version"]
    assert data["platform"]
    assert len(data["llm_calls"]) == 1
    call = data["llm_calls"][0]
    assert call["role"] == "other"
    assert call["model_id"] == "gpt-4o-mini"
    assert call["prompt_id"] == "p"
    assert call["prompt_version"] == "v1"
    assert call["usage"]["total_tokens"] == 15
    assert data["totals"]["llm_calls"] == 1
    assert data["totals"]["tokens"] == 15
    assert data["finalized_at"] is not None


def test_recorder_uses_provided_run_id(tmp_path: Path) -> None:
    rec = RunRecorder(runs_dir=tmp_path, run_id="fixed-id")
    assert rec.run_id == "fixed-id"
    assert rec.run_dir == tmp_path / "fixed-id"
