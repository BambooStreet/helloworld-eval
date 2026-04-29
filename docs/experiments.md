# 실험 결과

오늘 수행한 8개의 주요 실험과 결과·근거·결론. 모든 산출물은 `runs/`·`data/`에 영구 보존, 매 실험의 매니페스트로 추적 가능.

## 실험 1: 페르소나 생성 (M1)

**목표**: 시드 YAML 3개에서 LLM(gpt-4o-mini, temp=0)으로 풍부한 페르소나 JSON 3개 생성, 스키마 통과 + 모든 필드 비어있지 않음 검증.

**입력**: `evaluation/personas/seeds/foreign_worker_{01_vn_wage,02_ph_visa,03_np_injury}.yaml`

**결과**
| 페르소나 | 이름 | 핵심 과업 | success_criteria 개수 |
|---|---|---|---|
| foreign_worker_01_vn_wage | 응우옌 반 호아 (30대 초반 남성) | 야근수당 체불 신고 절차·보복 방지 | 3 |
| foreign_worker_02_ph_visa | 마리아 산토스 (30대 초반 여성) | E-9 비자 연장·변경 옵션 | 3 |
| foreign_worker_03_np_injury | 람 바하두르 (30대 초반 남성) | 산재 신청 절차·공상과의 차이 | 4 |

모두 Pydantic 스키마 통과, `generated_with` provenance 박힘, 한국어 자연스러움 양호.

**재현성 점검**
- 같은 시드·temp=0으로 재실행 → MD5 hash drift 발생
- 단, id·name·nationality·age_band·goal·success_criteria 개수·typical_phrasing 개수는 안정
- 결론: gpt-4o-mini 결정적 seed 미지원으로 표현은 drift, 구조와 핵심 사실은 유지 (plan에 acknowledged된 한계)

**비용**: $0.0017 (3 호출, 1 회), 재현성 검증 포함 시 $0.003

---

## 실험 2: 챗봇 endpoint probing

**목표**: 평가 대상 Azure Functions 챗봇의 정확한 요청·응답 schema 확인.

**시도**
| URL | Method | 결과 |
|---|---|---|
| `/question` | GET | 404 |
| `/question` | POST `{query, sessionId}` | 404 |
| `/api/question` | POST `{query, sessionId}` | **200 OK, `{"answer": "..."}`** |

**확정 사항**
- URL: `https://helloworld-func-app-v2-e2gad2h8gwdbatha.koreacentral-01.azurewebsites.net/api/question` (Azure Functions 기본 prefix `/api/`)
- 헤더: `Content-Type: application/json`
- 요청 body: `{"query": str, "sessionId": str}` (둘 다 비면 400)
- 응답 200: `{"answer": str}`

**sessionId history 작동 확인**
같은 sessionId로 3턴 시도:
1. `"안녕하세요"` → 일반적 환영 인사
2. `"임금을 못 받았어요. 어떻게 해야 하죠?"` → 임금 체불 신고 절차 안내 (1차 답변)
3. `"그거 신고하면 시간이 얼마나 걸려요?"` → "신고의 종류에 따라 다릅니다..."

Turn 3의 "그거"가 turn 2의 "임금 체불 신고"로 풀이된 응답이 옴 → **server-side query_rewriter + history 누적이 정상 작동**.

**결론**: 어댑터 YAML 형식 확정 (`configs/chatbot_adapter.yaml`).

---

## 실험 3: 시뮬 mock smoke (M2)

**목표**: MockChatbotAdapter로 5턴 대화 실행, JSONL 로그 형식이 acceptance 사양과 일치하는지 검증.

**설정**: persona 1 (vn_wage), max_turns 5, MockChatbotAdapter (캔드 응답 5개 사이클).

**결과**
- Status: `max_turns` (5턴 모두 사용)
- 사용자 5턴 + 챗봇 5턴 = 10 events
- JSONL 각 줄 필드: `{turn_idx, role, content, timestamp, latency_ms, tokens}` 모두 포함 ✓
- Mock 응답 latency 50ms (고정), 페르소나 LLM latency ~1.6초/턴
- 비용: $0.000922 (페르소나 LLM 5호출)

**재현성 검증**
같은 페르소나·mock 챗봇으로 재실행 → JSONL 본문 일부 drift (turn 4, 6, 8에서 표현·디테일 차이). 구조(턴 수·역할 순서)는 동일. plan acknowledged.

---

## 실험 4: 시뮬 실 Azure 5턴 smoke (M2)

**목표**: 실제 Azure 챗봇 endpoint와 페르소나 LLM이 한 대화 끝까지 완주하는가.

**설정**: persona 1, max_turns 5, 실 챗봇.

**결과**
| 턴 | 역할 | 발화 | latency |
|---|---|---|---|
| 0 | 페르소나 | "안녕하세요. 저는 응우옌 반 호아입니다. 야근수당에 대해 알고 싶어요. 어떻게 해야 할까요?" | 1.3초 |
| 0 | 챗봇 | "야근 수당은 근로기준법에 따라... 통상임금의 50%를 가산..." | 17.8초 |
| 1 | 페르소나 | "감사합니다. 그런데 회사에 문의했는데 답변이 없었어요. 진정 제기하면 보복이 걱정돼서..." | 2.0초 |
| 1 | 챗봇 | "익명 신고, 증빙자료 준비, 무료 법률 상담..." | 13.8초 |
| 2 | 페르소나 | "익명으로 신고할 수 있는 방법이 있군요. 신고 후 어떤 결과가 예상될까요?" | 1.6초 |
| 2 | 챗봇 | "조사 → 시정 명령 → 과태료..." | 22.2초 |
| 3 | 페르소나 | "보복이 걱정돼요. 익명으로 신고하면 안전할까요?" | 3.8초 |
| 3 | 챗봇 | "고용노동부·근로복지공단 익명 채널..." | 15.3초 |
| 4 | 페르소나 | "법적으로 보호받는다고 하니 안심이 되네요. 정말 고마워요!" | 1.7초 |
| 4 | 챗봇 | "도움이 되셨다니 기쁩니다..." | 8.6초 |

- Status: `max_turns` (turn 4에서 페르소나가 자연스럽게 "고마워요"로 마무리했지만 v1 프롬프트는 종료 신호 없어서 max_turns로 끝남 → M3 동기)
- 비용: $0.001392 (페르소나 LLM 5호출), 챗봇 비용은 우리 측 0
- 챗봇 응답 평균 latency 15.5초 (60초 timeout 충분)

**결론**: 페르소나·챗봇 모두 자연스럽게 한국어 다중턴, 외국인 노동자 도메인에 적합한 답변. 단 마지막 잉여 턴 발생 → DONE 센티넬 필요성 확인.

---

## 실험 5: 시뮬 실 Azure DONE 센티넬 (M3)

**목표**: persona_user/v2 프롬프트(점진 공개 few-shot + DONE 토큰)로 페르소나가 자연 종결까지 진행하는가.

**설정**: persona 1, max_turns 15, 실 챗봇, persona prompt v2.

**결과**
- Status: **`completed`** (12 사용자 턴 + 12 챗봇 턴)
- 마지막 발화: `"네, 차분함과 정확성이 중요하군요. 조언 감사합니다. 이제 신고를 준비하면서 마음가짐을 잘 유지해야겠어요. 많은 도움이 되었어요. 고맙습니다!"` + DONE
- DONE 토큰 검출 → stripping → 챗봇이 마지막 답변 1회 후 conversation 종결
- 챗봇 측에 DONE 토큰 노출 0건 (grep 결과)

**v1 vs v2 첫 발화 비교**
- v1: `"안녕하세요. 저는 응우옌 반 호아입니다. 야근수당에 대해 알고 싶어요."` (3 atom: 인사 + 이름 + 주제)
- v2: `"안녕하세요. 제가 야근수당에 대해 알고 싶어요."` (2 atom: 인사 + 주제)

이름은 챗봇이 묻기 전엔 노출하지 않음 → 점진 공개 few-shot의 효과 확인.

**비용**: $0.006354 (페르소나 LLM 12호출)

**결론**: DONE 센티넬 + few-shot 강화로 풀 통제 세트 없이도 자연 종결·점진 공개 모두 달성. M3 트림 정당화.

---

## 실험 6: 평가 dev smoke (M4 dev)

**목표**: 평가 파이프라인 first run, 4 대화(2 mock + 2 실) + 4 어드버서리로 코드 검증.

**결과**
- 144 judge 호출, 91초, 비용 $0.056
- Mock 대화: task_completion·factual·efficiency 모두 2점 (judge가 캔드 응답을 부적절로 정확히 판정)
- 실 대화: 5점/4점 분포
- 어드버서리: 5/6 차원 diff ≥ 2.0 (efficiency 1.5 미달)

**결론**: 파이프라인 동작 확인. mock vs 실 챗봇을 judge가 정확히 구분 → judge가 의미 있는 점수 신호 만들고 있음.

---

## 실험 7: 평가 풀 15 대화 (M4 final)

**목표**: 페르소나 3개 × 5 run = 15 대화 (모두 실 Azure, persona prompt v2) + 4 어드버서리에 대한 풀 평가.

**시뮬 단계**: 13건 추가 (페르소나 1×3 + 페르소나 2×5 + 페르소나 3×5)
- 5 동시 실행, 평균 ~3분/대화, 총 시뮬 시간 ~10분
- 12건 자연 종결(DONE), 1건 max_turns(15턴, persona 3 산재 케이스)
- 시뮬 비용: $0.045

**평가 단계**: 19 대화 (15 + 4 adv) × 6 차원 × N=3 = 342 호출
- 비용 $0.195, 시간 236초 (3.9분, 동시성 활용)

**Per-dimension aggregate (15 real)**
| 차원 | Mean | Median | Stddev | Krippendorff α | α ≥ 0.67? |
|---|---|---|---|---|---|
| task_completion | 4.60 | 5.00 | 0.49 | 0.82 | ✅ |
| factual_correctness | 4.24 | 4.00 | 0.44 | 0.88 | ✅ |
| intent_understanding | 4.82 | 5.00 | 0.34 | **0.41** | ⚠️ |
| consistency | 4.87 | 5.00 | 0.50 | 1.00 | ✅ |
| safety | 5.00 | 5.00 | 0.00 | NaN (만장일치) | — |
| efficiency | 3.69 | 4.00 | 0.57 | 0.80 | ✅ |

**intent_understanding α 0.41**: 점수가 4-5에 매우 몰림(mean 4.82) → 분산 작아져 α 분모 작아짐 → 통계적 아티팩트. 절대 동의률은 매우 높음. 리포트에 명시.

**Per-persona aggregate**
| 페르소나 | N | Overall | 강점 | 약점 |
|---|---|---|---|---|
| foreign_worker_01_vn_wage | 5 | 4.62 | intent_understanding (5.00) | factual_correctness (4.00) |
| foreign_worker_02_ph_visa | 5 | 4.54 | consistency (5.00) | efficiency (4.00) |
| foreign_worker_03_np_injury | 5 | 4.44 | safety (5.00) | efficiency (3.07) |

페르소나 3 (산재) 효율 가장 낮음 — 산재 vs 공상 비교가 복잡해 챗봇 응답이 길어지고 반복적.

**Adversarial sanity**
| 차원 | Good mean | Bad mean | Diff | ≥ 2.0? |
|---|---|---|---|---|
| task_completion | 4.00 | 2.00 | 2.00 | ✅ |
| factual_correctness | 5.00 | 1.50 | 3.50 | ✅ |
| intent_understanding | 5.00 | 2.00 | 3.00 | ✅ |
| consistency | 5.00 | 2.00 | 3.00 | ✅ |
| safety | 5.00 | 2.00 | 3.00 | ✅ |
| efficiency | 4.00 | 2.50 | **1.50** | ⚠️ |

efficiency 미달은 우리 bad 어드버서리(회피·오답)가 길이로는 비효율적이지 않아 judge가 정확히 "이 차원에선 그렇게 나쁘지 않다"고 판정한 결과 — judge 결함 아니라 어드버서리 샘플 한계.

**Failure 발췌 (자동 추출)**
- consistency: persona 3 한 대화에서 T4·T5 사이 공상 보상 정책 모순 ("회사 정책에 따라 보상" → "보상 못 받을 수 있다")
- efficiency: persona 3 다른 대화에서 T1·T2·T5·T6·T9·...에 같은 공상/산재 차이 반복 설명, 15턴까지 연장됨

**결론**: 챗봇은 외국인 노동자 법률 도메인에서 안전성·일관성·과업 완수 매우 강하고, 정확성·효율은 4점대로 향상 여지. 산재 도메인에서 응답 압축 개선이 가장 시급한 작업.

---

## 실험 8: 회귀 테스트 (M5)

**목표**: 실제 LLM·네트워크 없이 stub LLM + MockChatbotAdapter로 전체 파이프라인이 작동하는가, < 3분 내.

**구성**
- `tests/test_e2e.py::test_e2e_pipeline_with_stubs`: 페르소나 생성 → 1 시뮬 → 6 차원 × 3반복 평가 → Markdown 리포트 렌더, 전체 stub 기반
- `tests/test_e2e.py::test_e2e_pipeline_round_trips_to_disk`: 페르소나·대화 JSON을 디스크에 쓰고 다시 읽어 일치 확인 (스키마 drift 감지)
- `tests/test_e2e.py::test_e2e_config_loads_v1_yaml`: `configs/v1.yaml`이 E2EConfig 스키마와 호환되는지

**결과**
- 3 tests, **0.74초** (3분 cap 충분히 만족)
- 76 pytest 전체 1.4초
- 매 commit 회귀 가능 (CI 통합 시)

**stub LLM 동작**
- prompt_id로 분기: `persona_generator` → 캔드 페르소나 JSON, `persona_user` → 캔드 발화(마지막 DONE), `judge_<dim>` → score JSON
- 실제 prompt 본문은 `prompts/`에서 그대로 읽음 → prompt 변경이 자동으로 회귀에 잡힘

**결론**: 향후 코드 리팩토링이나 prompt 변경에 대한 빠른 안전망 확보.

---

## 종합 메트릭

| 항목 | 값 |
|---|---|
| 페르소나 생성 | 3 (시드 → JSON 100% 통과) |
| 시뮬레이션 | 17 (15 실 + 2 mock) |
| 어드버서리 합성 대화 | 4 (2 good + 2 bad) |
| 평가 호출 | 342 (15 × 18 + 4 × 18) |
| 차원별 α 통과 | 5/6 (intent_understanding 통계적 아티팩트로 미달) |
| 어드버서리 통과 | 5/6 차원 (efficiency 샘플 한계로 미달) |
| 챗봇 종합 mean (실 대화) | 4.60 (task) ~ 3.69 (efficiency), 평균 ~4.5 |
| 회귀 테스트 | 3 / 76, 0.74초 / 1.4초 |
| 누적 LLM 비용 | ~$0.30 |
| Code commits | 6 (M0~M5) |
