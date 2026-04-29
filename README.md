# evaluation

페르소나 LLM 기반 챗봇 평가 파이프라인.

## v1 범위
- 도메인: 외국인 노동자 법률/상담
- 언어: 한국어
- LLM: OpenAI gpt-4o-mini
- 페르소나: 3개

자세한 설계는 `/home/ohmyhong1/.claude/plans/breezy-dreaming-flute.md` 참고.

## 환경 준비

```sh
uv sync
export OPENAI_API_KEY=sk-...
```

## 사용법

```sh
# 실행 매니페스트만 생성 (sanity)
python -m evaluation.cli hello

# 페르소나 생성 (M1)
python -m evaluation.cli persona-generate ...

# 시뮬레이션 (M2~)
python -m evaluation.cli simulate ...

# E2E 평가 (M5)
python -m evaluation.cli run --config configs/v1.yaml
```

## 개발

```sh
uv run pytest
uv run ruff check .
uv run mypy
```
