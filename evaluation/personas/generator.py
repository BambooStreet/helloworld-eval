import asyncio
import json
from pathlib import Path
from typing import Any

import yaml

from ..core.llm import LLMClient
from ..core.manifest import RunRecorder
from ..core.prompts import Prompt
from ..core.settings import Settings
from .schema import Persona

GENERATOR_PROMPT_ID = "persona_generator"
GENERATOR_PROMPT_VERSION = "v1"


def load_seed(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Seed {path} must be a YAML mapping at top level")
    if "seed_id" not in data:
        raise ValueError(f"Seed {path} missing required field 'seed_id'")
    return data


async def generate_persona(
    client: LLMClient,
    prompt: Prompt,
    seed: dict[str, Any],
) -> Persona:
    seed_yaml = yaml.safe_dump(seed, allow_unicode=True, sort_keys=False)
    user_msg = f"## 입력 시드\n```yaml\n{seed_yaml}\n```"
    messages = [
        {"role": "system", "content": prompt.body},
        {"role": "user", "content": user_msg},
    ]
    result = await client.chat(
        role="persona",
        messages=messages,
        prompt=prompt,
        temperature=0.0,
        json_mode=True,
    )
    try:
        data = json.loads(result.content)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Generator returned invalid JSON for seed {seed['seed_id']}: {exc}\n"
            f"Content (first 500 chars): {result.content[:500]}"
        ) from exc

    return Persona.model_validate(
        {
            **data,
            "seed_id": seed["seed_id"],
            "generated_with": {
                "prompt_id": prompt.id,
                "prompt_version": prompt.version,
                "prompt_sha256": prompt.sha256,
                "model_id": result.record.model_id,
                "model_version": result.record.model_version,
                "temperature": result.record.temperature,
            },
        }
    )


def write_persona(persona: Persona, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / f"{persona.seed_id}.json"
    out.write_text(persona.model_dump_json(indent=2), encoding="utf-8")
    return out


def discover_seeds(seed_dir: Path) -> list[Path]:
    files = sorted(seed_dir.glob("*.yaml")) + sorted(seed_dir.glob("*.yml"))
    if not files:
        raise FileNotFoundError(f"No seed YAML files found in {seed_dir}")
    return files


async def generate_all_async(
    settings: Settings,
    recorder: RunRecorder,
    seed_dir: Path,
    output_dir: Path,
    client: LLMClient | None = None,
) -> list[Path]:
    prompt = Prompt.load(settings.prompts_dir, GENERATOR_PROMPT_ID, GENERATOR_PROMPT_VERSION)
    llm = client or LLMClient(settings=settings, recorder=recorder)
    seed_paths = discover_seeds(seed_dir)
    seeds = [load_seed(p) for p in seed_paths]
    personas = await asyncio.gather(*(generate_persona(llm, prompt, s) for s in seeds))
    return [write_persona(p, output_dir) for p in personas]


def default_seed_dir(settings: Settings) -> Path:
    return settings.project_root / "evaluation" / "personas" / "seeds"


def default_output_dir(settings: Settings) -> Path:
    return settings.data_dir / "personas"
