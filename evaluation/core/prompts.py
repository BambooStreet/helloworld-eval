import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Prompt:
    """Immutable prompt loaded from prompts/<id>/<version>.md.

    Changing a prompt means writing a new version file (e.g. v2.md), never editing v1.md.
    The sha256 is recorded in the run manifest so a stored result can be traced back to the
    exact prompt text that produced it.
    """

    id: str
    version: str
    body: str
    sha256: str

    @classmethod
    def load(cls, prompts_dir: Path, prompt_id: str, version: str) -> "Prompt":
        path = prompts_dir / prompt_id / f"{version}.md"
        if not path.is_file():
            raise FileNotFoundError(
                f"Prompt {prompt_id}/{version} not found at {path}. "
                f"Create the file before referencing this prompt."
            )
        body = path.read_text(encoding="utf-8")
        sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
        return cls(id=prompt_id, version=version, body=body, sha256=sha)
