# evaluation

페르소나 LLM 기반 챗봇 평가 파이프라인.

## v1 범위

- **도메인**: 외국인 노동자 법률·상담 (단일)
- **언어**: 한국어 (단일)
- **LLM**: OpenAI gpt-4o-mini (페르소나·judge 모두 동일 모델)
- **페르소나**: 3명 (베트남 임금체불 / 필리핀 비자 / 네팔 산재)
- **챗봇 어댑터**: POST `/api/question` `{query, sessionId}` → `{answer}` 1종

자세한 설계는 `/home/ohmyhong1/.claude/plans/breezy-dreaming-flute.md` 참고.

## 환경 준비

```sh
uv sync
cp .env.example .env   # 그리고 OPENAI_API_KEY 채우기
```

## 사용법

```sh
# 단일 명령으로 E2E (페르소나 → 시뮬 × N → 평가 → 리포트)
python -m evaluation.cli run --config configs/v1.yaml

# 단계별 실행
python -m evaluation.cli persona-generate
python -m evaluation.cli simulate --persona data/personas/<seed>.json --max-turns 15
python -m evaluation.cli evaluate --adv-good data/adversarial/good --adv-bad data/adversarial/bad

# 일부 단계 건너뛰기 (이전 산출물 재사용)
python -m evaluation.cli run --skip-personas --skip-simulation
```

## 개발

```sh
uv run pytest          # 76 tests, < 2초
uv run ruff check .
uv run mypy
```

회귀 테스트 (`tests/test_e2e.py`)는 stub LLM + MockChatbotAdapter로 전체 파이프라인을 1초 안에 검증합니다. 실제 OpenAI·Azure 호출은 없습니다.

## 산출물 위치

- `data/personas/*.json` — 시드 YAML에서 LLM이 확장한 페르소나
- `data/conversations/*.jsonl` — 시뮬 대화 로그 (턴별 timestamp·tokens·latency)
- `data/reports/eval_*.md` — 평가 리포트
- `data/adversarial/{good,bad}/*.json` — judge sanity check용 합성 대화
- `runs/{run_id}/manifest.yaml` — 모든 LLM 호출 메타·비용·prompt sha256 영구 보존

## v1 한계

신뢰성·확장성에 영향을 주는 알려진 한계:

- **단일 평가자**: 사람 평가 비교(Spearman ρ)는 v1에서 deferred. 현 시점 신뢰도는 Krippendorff α(judge 자체 일관성) + 어드버서리 sanity(좋음/나쁨 변별)로만 점검. 본격 검증은 사용자가 골든셋 20개 사람 평가를 채운 후 가능.
- **단일 도메인·언어**: 외국인 노동자 법률·한국어 외 케이스에서 페르소나 생성·judge rubric은 검증되지 않았음.
- **단일 LLM**: gpt-4o-mini 한 모델로 페르소나·judge·종료 판정 모두 처리. judge가 챗봇과 같은 모델 계열이라는 자기 평가 편향 가능성 있음.
- **gpt-4o-mini 결정적 seed 미지원**: temp=0이어도 같은 입력에 대해 출력이 미세하게 drift. 구조와 평가 결정은 manifest의 `prompt_sha256` + `model_version`으로 완전히 추적 가능하지만, 발화 본문은 100% bit-perfect로 재현되지 않음.
- **챗봇 어댑터 1종**: POST JSON 단일 형식만 지원. 스트리밍 응답·OAuth·gRPC 등은 v2.
- **사람 평가 미통합**: M5 acceptance에 명시된 ρ ≥ 0.7 검증은 미완. 현재는 judge 자체 일관성과 어드버서리 변별만으로 신뢰성을 추정.

ρ 미달 차원이 발견되면 다음 절차로 대응:
1. 해당 차원의 `prompts/judge_<dim>/v1.md`를 개정해 `v2.md`로 새 버전 저장
2. 오케스트레이터·평가기에서 prompt_version을 v2로 올림
3. 동일 골든셋에 대해 재평가, ρ 재측정
4. 통과 시 v2를 정식 버전으로, 미통과 차원은 리포트에 명시

## 비용

운영 시점 누적 LLM 비용 (gpt-4o-mini, 2026-04 기준):

| 단계 | 호출 수 | 비용 |
|---|---|---|
| 페르소나 생성 (3개) | 3 | ~$0.002 |
| 시뮬레이션 (페르소나 3 × run 5) | 페르소나 LLM 약 130 | ~$0.05 |
| 평가 (15 대화 × 6차원 × 3반복 + 어드버서리 4 × 18) | 342 | ~$0.20 |
| **E2E 1회 총합** | ~480 | **~$0.25** |
