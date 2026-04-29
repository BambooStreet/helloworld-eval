# 문서 색인

페르소나 LLM 기반 챗봇 평가 파이프라인 v1, 2026-04-29 1일 개발.

| 문서 | 내용 |
|---|---|
| [개발 로그](development.md) | M0~M5 마일스톤별로 무엇을 만들었고 왜 그렇게 만들었는지 |
| [실험 결과](experiments.md) | 8개 주요 실험과 결과 표·근거·결론 |

## 한 페이지 요약

**대상**: 외국인 노동자 법률·상담 챗봇 (Azure Functions, 한국어), `https://helloworld-func-app-v2-e2gad2h8gwdbatha.koreacentral-01.azurewebsites.net/api/question`

**파이프라인**
1. 시드 YAML 3개에서 LLM(gpt-4o-mini)으로 외국인 노동자 페르소나 JSON 확장
2. 페르소나 LLM이 챗봇 API와 다중턴 대화 수행, sessionId로 history 누적, `<<DONE>>` 센티넬로 자연 종결
3. 6 차원 judge LLM이 N=3회씩 1-5점 매김, Krippendorff α + 어드버서리 sanity로 신뢰성 검증
4. Markdown 리포트 + 매니페스트(prompt sha256 + model fingerprint) 영구 보존

**최종 결과**
- 페르소나 3개 × 5 run = **15 대화**, 모두 자연 종결 또는 max_turns 도달
- 챗봇 종합 점수 평균 **4.6/5** (task / intent / consistency / safety 5점, factual / efficiency 4점)
- 페르소나별 약점: vn_wage → factual, ph_visa → efficiency, np_injury → efficiency
- 어드버서리 sanity: 6 차원 중 5개에서 good vs bad diff ≥ 2.0
- Krippendorff α: 6 차원 중 5개에서 ≥ 0.67 (intent_understanding 0.41은 점수 분포 집중에 따른 통계적 아티팩트)

**누적 비용**: ~$0.30 (LLM, gpt-4o-mini)

**코드·테스트**
- 76 pytest 통과 (1.4초), ruff·mypy strict 무경고
- 회귀 테스트: stub LLM + mock 챗봇으로 전체 파이프라인 0.74초 검증

**리포지토리**: https://github.com/BambooStreet/helloworld-eval (`main` 브랜치, 6 commits)

**미완료 (deferred)**
- 사람 평가 골든셋 20개 채우기 + Spearman ρ 분석 (사용자 시간 소요)
- ρ 미달 시 rubric 개정 사이클 (절차는 README에 문서화됨)
