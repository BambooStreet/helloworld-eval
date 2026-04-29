# helloworld-eval (chatmodel)

외국인 비자/취업 법률 정보 챗봇 백엔드의 **로컬 평가 전용 슬림 버전**입니다. 인증 없이 클라이언트가 부여한 `sessionId` 단위로 멀티턴 대화 기록을 SQLite에 저장하며 번역+하이브리드 RAG로 답변을 생성합니다.

## 실행법

### 1. 의존성 설치

[uv](https://docs.astral.sh/uv/)로 관리합니다. `pyproject.toml`에 의존성, `uv.lock`에 정확한 버전이 고정돼 있습니다.

```bash
uv sync
```

`.venv/`가 생성되고 락파일에 따라 패키지가 설치됩니다.

의존성 추가/제거는 `uv add <패키지>` / `uv remove <패키지>`.

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

**질문 응답** (`sessionId`는 클라이언트가 임의로 부여; 동일 ID로 연속 호출하면 멀티턴이 됨)
```bash
curl -X POST http://127.0.0.1:8000/api/question \
  -H "Content-Type: application/json" \
  -d '{"sessionId":"test-001","query":"E-9 비자 갱신 방법"}'

# 같은 세션의 다음 질문 (직전 대화 맥락이 자동으로 반영됨)
curl -X POST http://127.0.0.1:8000/api/question \
  -H "Content-Type: application/json" \
  -d '{"sessionId":"test-001","query":"필요한 서류 알려줘"}'
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
    "sessionId": "test-001",
    "answer": "...",
    "translatedQuery": "...",
    "queryLang": "ko",
    "retrievedDocIds": ["..."]
  }
}
```

## 챗봇 응답 플로우

`POST /api/question` 한 번 호출 시 일어나는 일.

1. **요청 검증** (`main.py:question`) — `query`, `sessionId` 둘 다 비어있지 않은지 확인 (400)
2. **대화 기록 로드** — SQLite `messages` 테이블에서 해당 `sessionId`의 최근 10개 메시지를 시간 순으로 조회. 처음 보는 `sessionId`면 빈 리스트로 시작 (자동 신규 세션)
3. **질문 번역** — `TranslateModel.translate_query()` 한 번에 다음 3가지 동시 반환
   - `translated_query` — 한국어로 번역된 질문
   - `query_lang` — 원문 언어 (응답 언어 결정에 사용)
   - `mongo_query` — MongoDB aggregation 파이프라인 (구조화 텍스트 검색용)
4. **하이브리드 검색** (`query_model.py:hybrid_search`)
   - **1단계** — `mongo_query`가 있으면 `collection.aggregate()`로 키워드/텍스트 매칭
   - **2단계** — 1단계 결과가 `top_k=20`에 모자라면 OpenAI 임베딩(`text-embedding-3-large`) → `$vectorSearch`(`vector_index`)로 ANN 검색해 보충
   - 중복 제거 후 상위 `top_k`개 반환
5. **LLM 응답 생성** — 검색 문서 + 직전 대화 기록을 함께 `chat` 프롬프트 템플릿에 주입, `gpt-4o-mini`로 답변 생성. 답변 언어는 원문 언어(`query_lang`)에 맞춤
6. **대화 저장** — `messages` 테이블에 user 메시지 + bot 응답을 한 트랜잭션으로 insert

## 채팅 로그 저장 (`chat.db`)

SQLite 단일 테이블 스키마:

```sql
CREATE TABLE messages (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id  TEXT NOT NULL,
  sender      TEXT NOT NULL CHECK(sender IN ('user','bot')),
  content     TEXT NOT NULL,
  ts          TEXT NOT NULL          -- ISO8601 UTC
);
CREATE INDEX idx_messages_session_id ON messages(session_id, id);
```

- 서버 첫 실행 시 자동 생성 (idempotent)
- 별도의 `sessions` 테이블 없음 — `session_id`는 그냥 클라이언트가 정한 임의 문자열
- 검사: `sqlite3 chat.db "SELECT session_id, sender, substr(content,1,40), ts FROM messages ORDER BY id DESC LIMIT 20"`
- 초기화: 서버 정지 후 `rm chat.db`

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
pyproject.toml        # uv 프로젝트 메타 + 의존성 선언
uv.lock               # uv 락파일 (정확한 버전 고정)
chat.db               # (gitignored) 멀티턴 대화 로그 SQLite
```
