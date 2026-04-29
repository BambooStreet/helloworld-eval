# helloworld-eval (chatmodel)

외국인 비자/취업 법률 정보 챗봇 백엔드의 **로컬 평가 전용 슬림 버전**입니다. 인증·대화 히스토리·로그 DB 없이 단발성 질문 → 응답만 처리합니다.

## 실행법

### 1. 의존성 설치

`uv` 사용 시:
```bash
uv venv
uv pip install -r requirements.txt
```

`pip` 사용 시:
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 환경 변수

`.env.example`을 `.env`로 복사 후 채웁니다.

```bash
cp .env.example .env
```

| 키 | 설명 |
|---|---|
| `RAG_DATA_MONGODB_URI` | MongoDB Atlas 지식 베이스(`HelloWorld-AI.foreigner_legalQA_v3`) URI |
| `OPENAI_API_KEY` | OpenAI API 키 (`gpt-4o-mini`, `text-embedding-3-large` 사용) |

### 3. 서버 실행

```bash
.venv/bin/uvicorn main:app --reload
```

기본 포트는 `8000`. `Application startup complete.` 로그 뜨면 준비 완료.

### 4. 호출

**헬스체크**
```bash
curl "http://127.0.0.1:8000/api/get_echo_call?param=hi"
```

**질문 응답**
```bash
curl -X POST http://127.0.0.1:8000/api/question \
  -H "Content-Type: application/json" \
  -d '{"query":"E-9 비자 갱신 방법"}'
```

선택적으로 `mongo_query`(MongoDB aggregation 파이프라인)를 직접 넘겨 번역 단계의 자동 생성 결과를 덮어쓸 수 있습니다.

응답 본문은 다음 형태입니다.

```json
{
  "timestamp": "...",
  "path": "...",
  "status": 200,
  "error": "OK",
  "requestId": "...",
  "data": {
    "answer": "...",
    "translatedQuery": "...",
    "queryLang": "ko",
    "retrievedDocIds": ["..."]
  }
}
```

## 챗봇 응답 플로우

`POST /api/question` 한 번 호출 시 일어나는 일.

1. **요청 검증** (`main.py:question`) — JSON 본문에서 `query` 필드 추출 (빈 문자열은 400)
2. **질문 번역** — `TranslateModel.translate_query()` 한 번에 다음 3가지 동시 반환
   - `translated_query` — 한국어로 번역된 질문
   - `query_lang` — 원문 언어 (응답 언어 결정에 사용)
   - `mongo_query` — MongoDB aggregation 파이프라인 (구조화 텍스트 검색용)
3. **하이브리드 검색** (`query_model.py:hybrid_search`)
   - **1단계** — `mongo_query`가 있으면 `collection.aggregate()`로 키워드/텍스트 매칭
   - **2단계** — 1단계 결과가 `top_k=20`에 모자라면 OpenAI 임베딩(`text-embedding-3-large`) → `$vectorSearch`(`vector_index`)로 ANN 검색해 보충
   - 중복 제거 후 상위 `top_k`개 반환
4. **LLM 응답 생성** — 검색 문서들을 컨텍스트로 합쳐 `chat` 프롬프트 템플릿에 주입, `gpt-4o-mini`로 답변 생성. 답변 언어는 원문 언어(`query_lang`)에 맞춤
5. **로컬 로그 적재** — 매 요청마다 `logs/chat_test.jsonl`에 한 줄 append
   ```json
   {"timestamp":"...","query":"...","translatedQuery":"...","queryLang":"ko","mongoQuery":[...],"retrievedDocIds":["..."],"answer":"..."}
   ```

대화 히스토리는 항상 빈 리스트로 호출되므로 모든 질문은 단발성으로 처리됩니다.

## 주요 설정 (`configs/config.json`)

| 키 | 값 | 설명 |
|---|---|---|
| `chat_config.model` | `gpt-4o-mini` | 응답 생성 모델 |
| `chat_config.temperature` | `0.7` | |
| `chat_config.top_k` | `20` | 하이브리드 검색 최종 문서 수 |
| `chat_config.numCandidates` | `200` | ANN 후보 수 |
| `data_config.embedding_model` | `text-embedding-3-large` | |
| `path.db_name` / `collection_name` | `HelloWorld-AI` / `foreigner_legalQA_v3` | RAG 컬렉션 |

## 디렉토리

```
.env.example          # 환경변수 템플릿
configs/config.json   # 모델/검색 파라미터, RAG 컬렉션 경로
main.py               # FastAPI 앱 + ChatService
query_model.py        # 하이브리드 검색 + 응답 생성
translate_model.py    # 질문 번역 + mongo_query 파이프라인 생성
prompts/prompts.py    # 프롬프트 템플릿
utils.py              # parse_mongo_query
requirements.txt      # 런타임 의존성
logs/                 # (gitignored) 요청별 JSONL 로그
```
