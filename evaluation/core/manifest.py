import hashlib
import platform
import secrets
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .models import LLMCallRecord, Manifest


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _lock_hash(project_root: Path) -> str | None:
    for name in ("uv.lock", "poetry.lock", "requirements.lock", "requirements.txt"):
        path = project_root / name
        if path.is_file():
            return hashlib.sha256(path.read_bytes()).hexdigest()
    return None


def new_run_id() -> str:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(3)
    return f"{ts}-{suffix}"


class RunRecorder:
    """Owns a Manifest in memory and writes it to runs/<run_id>/manifest.yaml on finalize()."""

    def __init__(
        self,
        runs_dir: Path,
        run_id: str | None = None,
        config: dict[str, Any] | None = None,
        project_root: Path | None = None,
    ) -> None:
        self.run_id = run_id or new_run_id()
        self.run_dir = runs_dir / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        root = project_root or Path.cwd()
        self.manifest = Manifest(
            run_id=self.run_id,
            created_at=datetime.now(UTC),
            git_commit=_git_commit(),
            requirements_lock_hash=_lock_hash(root),
            python_version=sys.version.split()[0],
            platform=platform.platform(),
            config=config or {},
        )

    def add_llm_call(self, record: LLMCallRecord) -> None:
        self.manifest.llm_calls.append(record)

    @property
    def manifest_path(self) -> Path:
        return self.run_dir / "manifest.yaml"

    def finalize(self) -> Path:
        self.manifest.finalized_at = datetime.now(UTC)
        data = self.manifest.model_dump(mode="json")
        data["totals"] = {
            "llm_calls": len(self.manifest.llm_calls),
            "tokens": self.manifest.total_tokens,
            "usd_cost": round(self.manifest.total_cost_usd, 6),
        }
        with self.manifest_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
        return self.manifest_path
