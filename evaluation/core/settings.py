from pathlib import Path

from pydantic import BaseModel, Field


class Settings(BaseModel):
    model_id: str = "gpt-4o-mini"

    persona_timeout_s: float = 30.0
    judge_timeout_s: float = 30.0
    chatbot_timeout_s: float = 60.0

    max_retries: int = 3
    retry_base_delay_s: float = 1.0

    persona_concurrency: int = 5
    judge_concurrency: int = 5
    chatbot_concurrency: int = 3

    project_root: Path = Field(default_factory=Path.cwd)

    @property
    def prompts_dir(self) -> Path:
        return self.project_root / "prompts"

    @property
    def runs_dir(self) -> Path:
        return self.project_root / "runs"

    @property
    def data_dir(self) -> Path:
        return self.project_root / "data"

    @property
    def configs_dir(self) -> Path:
        return self.project_root / "configs"
