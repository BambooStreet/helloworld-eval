import pytest


@pytest.fixture(autouse=True)
def fake_openai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """AsyncOpenAI() raises on init if no API key. Use a fake key in all tests
    since we mock the underlying chat.completions.create call."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake")
