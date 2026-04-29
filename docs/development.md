# 개발 로그 (v1, 2026-04-29)

마일스톤별로 무엇을 만들었고 어떤 설계 결정을 했는지 기록.

## 전체 구조

```
evaluation/
├── core/         # LLM 래퍼, 매니페스트, 프롬프트 로더, 설정
├── personas/     # 시드 YAML → 페르소나 JSON 생성기
├── simulator/    # 챗봇 어댑터 + 다중턴 오케스트레이터
├── evaluator/    # Judge × N=3 + Krippendorff α + Markdown 리포트
├── runner.py     # E2E 단계 시퀀싱
└── cli.py        # Typer CLI 진입점

prompts/          # 불변 버전 프롬프트 (persona_generator/v1, persona_user/v2, judge_<dim>/v1)
configs/          # E2E 실행 설정, 챗봇 어댑터 설정
data/             # 페르소나·대화·리포트 산출물
runs/             # 매 실행의 매니페스트 (LLM 호출 메타·비용·prompt sha256)
tests/            # 76 pytest
```

---

## M0 — 프로젝트 골격 + 재현성·운영 인프라

**목표**: 이후 모든 마일스톤이 공유하는 인프라를 깔기. 특히 **재현성**과 **운영 속성**(timeout/retry/cost)이 처음부터 박혀 있어야 나중에 새지 않음.

**만든 것**
- `core/llm.py`: `LLMClient` (OpenAI 비동기 래퍼) + `retry_async` (지수 백오프 5xx/429/네트워크 재시도, 4xx 즉시 실패)
- `core/manifest.py`: `RunRecorder` — 매 실행마다 `runs/{run_id}/manifest.yaml`에 git_commit, lock_hash, python_version, platform, 모든 LLM 호출의 (model_id, prompt_id/version/sha256, temperature, seed, response_format, tokens, usd_cost, latency, attempts) 기록
- `core/prompts.py`: `Prompt.load()` — `prompts/<id>/<version>.md` 불변 파일 로더 + sha256 자동 계산
- `core/models.py`: Pydantic 모델 (LLMUsage, LLMCallRecord, Manifest)
- `core/settings.py`: timeout·동시성·재시도 설정
- `cli.py`: Typer 진입점 (`hello` sanity 명령)

**핵심 설계 결정**
- **OpenAI SDK 위에 한 겹 더 래핑한 이유**: SDK 기본 재시도가 4xx도 잡아버려서 의도 안 맞음. 호출 메타 자동 기록(prompt sha256·역할·비용)도 SDK 밖 책임. 역할별 동시성 세마포어(persona/judge/chatbot 3개 풀)도 별도 필요.
- **프롬프트 불변 파일 + sha256 자동 기록**: 변경 시 v2.md 새 파일을 만들고, manifest의 `prompt_version` + `prompt_sha256`로 그 시점 정확한 본문이 추적 가능. 이게 평가 결과의 출처 추적 핵심.
- **gpt-4o-mini 가격 하드코딩**: 모델·가격 변동 시 한 곳에서 수정. 매 호출마다 USD 비용 계산해 매니페스트에 기록.

**완료 기준 충족**
- 18 tests, ruff·mypy 무경고
- `python -m evaluation.cli hello` → 매니페스트 생성 (LLM 호출 0건으로도 동작)
- timeout/retry 단위 테스트 통과 (httpx mock)

---

## M1 — 페르소나 생성기

**목표**: 시드 YAML(국적·직업·핵심 상황·과업)을 LLM으로 풍부한 페르소나 JSON으로 확장.

**만든 것**
- `personas/seeds/foreign_worker_*.yaml`: 시드 3개
  1. 응우옌 반 호아 (베트남, 자동차 부품 제조, 임금 체불)
  2. 마리아 산토스 (필리핀, 가사도우미, E-9 비자 변경)
  3. 람 바하두르 (네팔, 건설, 산재 vs 공상)
- `prompts/persona_generator/v1.md`: 시스템 프롬프트 (스키마 정의 + 출력 규칙)
- `personas/schema.py`: Pydantic Persona (demographics·context·task·success_criteria·communication_style·background_story + generated_with provenance)
- `personas/generator.py`: 시드 → 페르소나 LLM 호출 + JSON 파싱 + 스키마 검증
- CLI `persona-generate`

**핵심 설계 결정**
- **시드 + LLM 확장 방식 (vs 사람이 풀 페르소나 직접 작성)**: 시드는 사실의 핵심만 담고, 디테일(거주지·심리 상태·자주 쓰는 표현)은 LLM이 살을 붙임. 사람 작성 시간을 줄이면서도 시드의 사실은 보존됨. JSON mode + Pydantic 스키마 검증으로 출력 형식이 흔들리면 즉시 에러.
- **`success_criteria`를 페르소나에 박아넣음**: M4 judge가 task_completion을 평가할 때 직접 참조. 페르소나가 단순히 "임금 받고 싶음"이 아니라 "신고 절차 단계별 안내됨, 보복 방지 방법 1개 이상 안내됨" 같이 구체적인 성공 조건을 가지게 됨.
- **`generated_with` 메타 필드**: 어떤 prompt·모델·temperature로 만들어졌는지 페르소나 JSON에 박혀 있어 한 달 뒤에도 출처 추적 가능.

**완료 기준 충족**
- 시드 3개 → 페르소나 JSON 3개 모두 생성, 모든 필드 비어있지 않음
- 동일 입력 재실행 시 hash drift 발생(gpt-4o-mini seed 미지원)이지만 구조·핵심 필드 안정 — manifest에서 두 실행 비교 가능
- 비용: $0.0017 / 3 페르소나

---

## M2 — 시뮬레이터 v0 (통제 미적용)

**목표**: YAML config로 정의된 챗봇 API와 페르소나 LLM이 다중턴 대화. 50턴 cap, JSONL 로그.

**만든 것**
- `simulator/adapter.py`:
  - `HttpChatbotAdapter` (YAML config: endpoint, method, headers, request_body 템플릿, dot-path response 추출)
  - `${user_message}`·`${session_id}` 치환, 5xx/429 재시도, dot-path 응답 추출
  - `MockChatbotAdapter` (in-memory, 캔드 응답 사이클, 결정적)
- `simulator/orchestrator.py`: `run_conversation` 다중턴 루프, 50턴 cap, `status="failed"` 시 부분 로그 보존
- `simulator/persona_chat.py`: 페르소나 시스템 프롬프트 렌더러 (`${name}` 등 치환)
- `prompts/persona_user/v1.md`: 페르소나 역할연기 프롬프트
- `configs/chatbot_adapter.yaml`: 실제 Azure 엔드포인트 설정
- CLI `simulate`

**핵심 설계 결정**
- **챗봇 endpoint 사전 probing**: M2 시작 전 curl로 `POST /question` → 404, `POST /api/question` → 200, 응답 `{"answer": "..."}` 확정. Azure Functions 기본 prefix `/api/` 발견.
- **sessionId 전략 `eval-{run_id}-{seed_id}`**: 챗봇이 server side SQLite에 history를 sessionId 키로 누적. 우리가 매 턴 풀 history를 보낼 필요 없음 — 단발 메시지만. 운영자가 DB에서 평가 트래픽만 grep·삭제하기 쉬운 형식.
- **두 history 평행 유지**: 우리도 페르소나 다음 발화·judge 입력용으로 history를 따로 들고 있음. 챗봇 server-side history와 내용은 같지만 분리해서 관리.
- **YAML config 기반 어댑터**: 챗봇이 다른 챗봇으로 바뀌거나 schema가 변하면 코드 변경 없이 YAML만 수정.

**완료 기준 충족**
- 22 새 tests, retry/dot-path/template 검증
- Mock 챗봇 5턴 smoke + 실제 Azure 5턴 smoke 모두 성공
- sessionId history 누적 작동 확인 (Turn 3 "그거" → Turn 1·2 맥락에서 대명사 풀이됨)

---

## M3 (트림됨) — DONE 센티넬 종료 + 페르소나 프롬프트 v2

**원래 계획**: info-atom 휴리스틱 게이트 + LLM 게이트 + `allowed_atoms` 동적 계산 + 위반 재시도 + 별도 termination judge + 자연스러움 옵션.

**검토 결과 YAGNI 판정 — 풀세트 전부 드롭**
- M2 스모크에서 페르소나가 시스템 프롬프트 규칙만으로도 한 턴에 1-2 atom씩 자연스럽게 흘림
- 풀세트 도입 시 페르소나 LLM 호출이 턴당 **1회 → 2~5회로 증가**
- 보호하려던 문제(정보 폭발)가 실제로 거의 발생하지 않음

**대신 만든 것 (저렴한 대체안)**
- `prompts/persona_user/v2.md`: 점진 공개 few-shot 예시(좋음/나쁨 한 쌍) 추가, 행동 규칙 7번에 `<<DONE>>` 센티넬 규칙 추가
- 오케스트레이터: DONE 토큰 검출 → stripping → status="completed"로 종결. 추가 LLM 호출 0.

**M4로 이동**: atom 수는 평가 단계에서 judge가 사후 분석 메트릭으로 활용 (생성 시점 통제가 아니라 측정만).

**효과 (실측)**
- v1: turn 0에서 페르소나가 자기 이름까지 자발적 노출 ("저는 응우옌 반 호아입니다...")
- v2: turn 0이 "야근수당에 대해 알고 싶어요" 한 가지로 압축
- 12턴 실 챗봇 대화에서 페르소나가 자연 종결 ("많은 도움이 되었어요. 고맙습니다!" + DONE), `status="completed"` 마감
- 비용: $0.006 (12턴 풀 대화), 풀세트 도입했으면 2-5배

**완료 기준 충족**: 위 실측, 2 추가 단위 tests (DONE 종결, DONE 단독)

---

## M4 — 평가기 + judge 신뢰성

**목표**: LLM-as-judge로 6 차원 1-5점 평가, N=3회 중앙값, Krippendorff α 신뢰성 측정, 어드버서리 sanity, Markdown 리포트.

**만든 것**
- `prompts/judge_<dim>/v1.md` × 6: task_completion / factual_correctness / intent_understanding / consistency / safety / efficiency
- `evaluator/judges.py`: 차원별 N=3 호출 + 중앙값/평균/표준편차, JSON 강제, transcript+페르소나 입력 렌더러
- `evaluator/reliability.py`: Krippendorff α(ordinal), 어드버서리 good vs bad diff, per-persona aggregate
- `evaluator/reporter.py`: Markdown 리포트 (집계·페르소나·대화 디테일·실패 발췌)
- `data/adversarial/{good,bad}/*.json` × 4: 손으로 작성한 명백히 좋은/나쁜 합성 대화
- CLI `evaluate`

**핵심 설계 결정**
- **6 차원 분리 호출 (vs 한 번에 6개 점수)**: 한 호출에 묶으면 anchoring 편향(첫 차원 점수가 다른 차원에 전염), 하나의 system prompt에 6 rubric 압축으로 calibration 흐려짐, 한 차원 JSON 파싱 실패 시 전체 폐기. 호출이 6배 들어도 차원별 정확도가 우선.
- **N=3 중앙값**: temp=0이어도 LLM judge가 가끔 한 발 튐. 중앙값으로 outlier 흡수 + N=3 사이 표준편차로 어느 차원 rubric이 모호한지 데이터로 드러남. 실측 결과 거의 모든 차원에서 stddev 0 (3 raters 만장일치) — N=3이 충분.
- **Krippendorff α 선택**: 1-5 ordinal scale에서 Cohen/Fleiss κ는 nominal로 다뤄 부적합. α는 ordinal 직접 지원, 결측·rater 수 변동 허용, 임계 0.67이 표준 가이드라인.
- **어드버서리 sanity 필요성**: α는 "judge끼리 일관"만 측정 — 함께 틀려도 α 1.0 가능. 어드버서리는 "명백히 좋은 5점 / 나쁜 1점을 실제로 차별화하는가"의 외부 잣대. 인간 평가 없이도 빠른 회귀 가능.
- **페르소나 정보를 judge에 같이 입력**: task_completion은 success_criteria 봐야 평가 가능. intent_understanding은 페르소나 한국어 수준 봐야 평가 가능. safety는 페르소나 취약성(외국인·언어 약자) 봐야 평가 가능. judge가 *이 사용자에게* 좋은 답인지를 본다.

**완료 기준 결과**
- 15 대화 + 4 어드버서리 평가, 차원별/페르소나별/실패 발췌 모두 포함
- Krippendorff α 5/6 ≥ 0.67 (intent_understanding 0.41 — 점수 분포가 4-5에 몰린 데서 오는 통계적 아티팩트, 리포트에 명시)
- 어드버서리 5/6 차원 diff ≥ 2.0 (efficiency 1.5: bad 어드버서리가 길이 비효율 케이스 부재)
- 비용: $0.20 (eval) + $0.045 (sims), 시간 236초

---

## M5 (트림됨) — 단일 E2E 명령 + 회귀 테스트

**원래 계획**: 단일 명령 + 골든셋 20개 사람 평가 + Spearman ρ 분석 + 회귀 테스트 + README v1 한계.

**트림**: 사람 평가 부분(2-3시간 사용자 작업)은 deferred. 코드 작업만 우선.

**만든 것**
- `evaluation/runner.py`: `E2EConfig` Pydantic, `batch_simulate` (asyncio.Semaphore로 동시성 제어), `evaluate_all`, `run_e2e` 단계 시퀀싱
- `configs/v1.yaml`: 프로덕션 설정 (n_runs_per_persona=5, max_turns=15, sim_concurrency=3, n_judge_repetitions=3)
- CLI `run` 명령 + `--skip-personas`, `--skip-simulation`, `--skip-evaluation` 옵션
- `tests/test_e2e.py`: stub LLM + MockChatbotAdapter로 전체 파이프라인 0.74초 회귀 (3 tests)
- README v1 한계 섹션 + ρ 미달 시 rubric 개정 절차 문서화

**핵심 설계 결정**
- **stub LLM이 prompt id로 분기**: persona_generator → 캔드 페르소나 JSON, persona_user → 캔드 발화(마지막에 DONE), judge_<dim> → 점수 JSON. 실제 prompt 본문은 변경되지 않으므로 prompt 변경이 회귀에 잡힘.
- **runner 모듈 분리**: CLI 디스패치와 stage 로직 분리해서 통합 테스트가 Typer 거치지 않고 직접 호출 가능.
- **각 stage 별도 RunRecorder**: 단계별 매니페스트 보존 (페르소나 생성 + N×P 시뮬 + 평가). 단일 umbrella manifest는 v2로 deferred.

**완료 기준 결과**
- `python -m evaluation.cli run --config configs/v1.yaml`로 단일 명령 E2E 가능
- 회귀 테스트 3건 0.74초 (3분 cap 충분히 만족)
- 76 pytest pass, ruff·mypy strict clean

---

## YAGNI 트림된 항목 목록

| 마일스톤 | 항목 | 트림 이유 |
|---|---|---|
| M3 | info-atom 휴리스틱 게이트 | 페르소나가 이미 자연스러운 점진 공개 |
| M3 | info-atom LLM 게이트 (턴당 +1 호출) | 비용 2배인데 케이스 5-10%만 차단 |
| M3 | `allowed_atoms` 동적 계산 | 위 항목에 종속 |
| M3 | 위반 시 재시도 (최대 3회) | 비용 폭증, 효용 낮음 |
| M3 | 별도 termination judge | DONE 센티넬로 추가 LLM 호출 0건에 대체 |
| M3 | 자연스러움 옵션 (오타·되묻기) | 챗봇 query_rewriter가 풀어줘서 평가 신호만 흐림 |
| M5 | 사람 평가 골든셋 | 사용자 시간 가용 시 추가 (코드 측 미완) |
| M5 | Spearman ρ 분석 스크립트 | 사람 평가 데이터 들어온 후 |
| M5 | umbrella E2E manifest | stage별 manifest로도 충분, v2로 deferred |

## 누적 비용

| 활동 | 비용 |
|---|---|
| M1 페르소나 생성 (2회 — 재현성 검증 포함) | $0.003 |
| M2 시뮬 (mock 2 + 실 2) | $0.003 |
| M3 시뮬 (실 12턴) | $0.006 |
| M4 시뮬 13건 추가 + 평가 풀 | $0.24 |
| 어드버서리 평가 (M4 dev + final 포함) | $0.06 |
| **누계** | **~$0.30** |

## 시간

오전 ~ 오후 1일치. M0 → M5 트림 완료. 회귀 테스트 1.4초.
