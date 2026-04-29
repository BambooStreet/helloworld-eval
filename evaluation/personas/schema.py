from pydantic import BaseModel, Field


class Demographics(BaseModel):
    age_band: str
    gender: str
    nationality: str
    occupation: str
    residence: str
    korean_proficiency: str
    digital_literacy: str


class UsageContext(BaseModel):
    when: str
    where: str
    device: str
    emotional_state: str


class Task(BaseModel):
    goal: str
    success_criteria: list[str] = Field(min_length=1)


class CommunicationStyle(BaseModel):
    tone: str
    info_disclosure: str
    complaint_expression: str
    typical_phrasing: list[str] = Field(min_length=1)
    quirks: list[str] = Field(default_factory=list)


class GeneratedWith(BaseModel):
    prompt_id: str
    prompt_version: str
    prompt_sha256: str
    model_id: str
    model_version: str | None = None
    temperature: float


class Persona(BaseModel):
    id: str
    name: str
    seed_id: str
    demographics: Demographics
    context: UsageContext
    task: Task
    background_story: str = Field(min_length=50)
    communication_style: CommunicationStyle
    generated_with: GeneratedWith
