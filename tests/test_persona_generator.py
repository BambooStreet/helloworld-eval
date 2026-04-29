"""Generator tests use a fake LLMClient that returns canned JSON, so we exercise
parsing/validation/file IO without spending tokens. The real-LLM smoke test is
manual and run separately."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml

from evaluation.core.llm import LLMResult
from evaluation.core.models import LLMCallRecord, LLMUsage
from evaluation.core.prompts import Prompt
from evaluation.personas.generator import (
    discover_seeds,
    generate_persona,
    load_seed,
    write_persona,
)
from tests.test_persona_schema import _full_persona_dict


# Helper: wrap a value in an awaitable for MagicMock(return_value=...).
async def _async_value(value: Any) -> Any:
    return value


def _fake_llm_result(content_dict: dict[str, Any]) -> LLMResult:
    record = LLMCallRecord(
        timestamp=datetime.now(UTC),
        role="persona",
        model_id="gpt-4o-mini",
        model_version="fp_test",
        prompt_id="persona_generator",
        prompt_version="v1",
        prompt_sha256="a" * 64,
        temperature=0.0,
        usage=LLMUsage(prompt_tokens=100, completion_tokens=200, total_tokens=300),
        usd_cost=0.0,
        latency_ms=10.0,
    )
    return LLMResult(content=json.dumps(content_dict, ensure_ascii=False), record=record)


def _fake_prompt() -> Prompt:
    return Prompt(id="persona_generator", version="v1", body="...", sha256="a" * 64)


async def test_generate_persona_parses_and_validates(tmp_path: Path) -> None:
    canned = _full_persona_dict()
    # The generator strips/overrides seed_id and generated_with, so they don't need to match here.
    canned.pop("seed_id")
    canned.pop("generated_with")

    fake_client = MagicMock()
    fake_client.chat = MagicMock(return_value=_async_value(_fake_llm_result(canned)))

    seed = {"seed_id": "p_test", "core_situation": "x"}
    persona = await generate_persona(fake_client, _fake_prompt(), seed)

    assert persona.seed_id == "p_test"
    assert persona.generated_with.prompt_id == "persona_generator"
    assert persona.demographics.nationality == "베트남"


async def test_generate_persona_invalid_json_raises(tmp_path: Path) -> None:
    record = LLMCallRecord(
        timestamp=datetime.now(UTC),
        role="persona",
        model_id="gpt-4o-mini",
        temperature=0.0,
        usage=LLMUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        usd_cost=0.0,
        latency_ms=1.0,
    )
    fake_client = MagicMock()
    fake_client.chat = MagicMock(
        return_value=_async_value(LLMResult(content="not json at all", record=record))
    )

    with pytest.raises(ValueError, match="invalid JSON"):
        await generate_persona(fake_client, _fake_prompt(), {"seed_id": "x"})


def test_load_seed_reads_yaml_with_seed_id(tmp_path: Path) -> None:
    p = tmp_path / "s.yaml"
    p.write_text("seed_id: foo\nname_hint: bar\n", encoding="utf-8")
    data = load_seed(p)
    assert data["seed_id"] == "foo"


def test_load_seed_missing_seed_id_raises(tmp_path: Path) -> None:
    p = tmp_path / "s.yaml"
    p.write_text("name: foo\n", encoding="utf-8")
    with pytest.raises(ValueError, match="seed_id"):
        load_seed(p)


def test_load_seed_non_mapping_raises(tmp_path: Path) -> None:
    p = tmp_path / "s.yaml"
    p.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_seed(p)


def test_write_persona_writes_json_under_seed_id(tmp_path: Path) -> None:
    from evaluation.personas.schema import Persona

    persona = Persona.model_validate(_full_persona_dict())
    path = write_persona(persona, tmp_path)

    assert path.name == "p_test.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["seed_id"] == "p_test"
    assert data["communication_style"]["typical_phrasing"]


def test_discover_seeds_sorted_and_nonempty(tmp_path: Path) -> None:
    (tmp_path / "b.yaml").write_text("seed_id: b\n", encoding="utf-8")
    (tmp_path / "a.yaml").write_text("seed_id: a\n", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("", encoding="utf-8")
    files = discover_seeds(tmp_path)
    assert [f.name for f in files] == ["a.yaml", "b.yaml"]


def test_discover_seeds_empty_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        discover_seeds(tmp_path)


def test_real_seeds_parse_and_have_required_fields() -> None:
    seed_dir = Path(__file__).resolve().parent.parent / "evaluation" / "personas" / "seeds"
    files = discover_seeds(seed_dir)
    assert len(files) == 3
    for f in files:
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        assert data["seed_id"]
        assert data["nationality"]
        assert data["sector"]
        assert data["core_situation"].strip()
        assert data["core_task"].strip()


