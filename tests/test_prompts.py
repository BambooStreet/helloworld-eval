from pathlib import Path

import pytest

from evaluation.core.prompts import Prompt


def test_prompt_load_reads_body_and_hashes(tmp_path: Path) -> None:
    pdir = tmp_path / "test_prompt"
    pdir.mkdir()
    body = "Hello, world.\n페르소나 시스템 프롬프트.\n"
    (pdir / "v1.md").write_text(body, encoding="utf-8")

    p = Prompt.load(tmp_path, "test_prompt", "v1")
    assert p.id == "test_prompt"
    assert p.version == "v1"
    assert p.body == body
    assert len(p.sha256) == 64


def test_prompt_load_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        Prompt.load(tmp_path, "missing", "v1")


def test_prompt_sha_changes_with_body(tmp_path: Path) -> None:
    pdir = tmp_path / "p"
    pdir.mkdir()
    (pdir / "v1.md").write_text("a", encoding="utf-8")
    (pdir / "v2.md").write_text("ab", encoding="utf-8")

    a = Prompt.load(tmp_path, "p", "v1")
    b = Prompt.load(tmp_path, "p", "v2")
    assert a.sha256 != b.sha256
