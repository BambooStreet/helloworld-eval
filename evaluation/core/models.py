from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

LLMRole = Literal[
    "persona",
    "judge",
    "termination",
    "info_atom",
    "allowed_atoms",
    "other",
]


class LLMUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class LLMCallRecord(BaseModel):
    timestamp: datetime
    role: LLMRole
    model_id: str
    model_version: str | None = None
    prompt_id: str | None = None
    prompt_version: str | None = None
    prompt_sha256: str | None = None
    temperature: float
    top_p: float | None = None
    seed: int | None = None
    response_format: str | None = None
    usage: LLMUsage
    usd_cost: float
    latency_ms: float
    attempts: int = 1


class Manifest(BaseModel):
    run_id: str
    created_at: datetime
    finalized_at: datetime | None = None
    git_commit: str | None = None
    requirements_lock_hash: str | None = None
    python_version: str
    platform: str
    config: dict[str, object] = Field(default_factory=dict)
    llm_calls: list[LLMCallRecord] = Field(default_factory=list)

    @property
    def total_cost_usd(self) -> float:
        return sum(c.usd_cost for c in self.llm_calls)

    @property
    def total_tokens(self) -> int:
        return sum(c.usage.total_tokens for c in self.llm_calls)
