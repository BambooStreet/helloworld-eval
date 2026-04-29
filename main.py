import os
import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from http import HTTPStatus

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pymongo import MongoClient

from utils import parse_mongo_query
from translate_model import TranslateModel
from query_rewriter import QueryRewriter

load_dotenv()


# Ensure INFO logs are emitted when running locally
logger = logging.getLogger()
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)
logger.setLevel(logging.INFO)

app = FastAPI()

_chat_service = None
_service_lock = threading.Lock()

DB_PATH = os.path.join(os.path.dirname(__file__), "chat.db")
HISTORY_LIMIT = 10


def _build_base_payload(request: Request, status_code: int, error: str, request_id: str):
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "path": str(request.url) if request else "",
        "status": status_code,
        "error": error,
        "requestId": request_id,
    }


def create_response(
    request: Request,
    status_code: int = 200,
    data: dict | list | str | None = None,
    error: str | None = None,
    details: dict | None = None,
) -> JSONResponse:
    """Spring WebFlux DefaultErrorAttributes 스타일의 공통 응답을 생성합니다."""
    request_id = (
        request.headers.get("x-request-id")
        or request.headers.get("x-ms-request-id")
        or str(uuid.uuid4())
    )
    if error is None:
        try:
            error_text = HTTPStatus(status_code).phrase
        except ValueError:
            error_text = ""
    else:
        error_text = error
    payload = _build_base_payload(request, status_code, error_text, request_id)
    if details is not None:
        payload["details"] = details
    if data is not None:
        payload["data"] = data

    return JSONResponse(content=payload, status_code=status_code)


async def get_chat_service():
    """ChatService 싱글톤 (thread-safe)"""
    global _chat_service
    if _chat_service is None:
        with _service_lock:
            if _chat_service is None:
                _chat_service = ChatService()
                _chat_service.initialize()
    return _chat_service


@app.get("/api/get_echo_call")
async def get_echo_call(request: Request, param: str | None = None):
    """헬스체크용 에코 엔드포인트."""
    logging.info("Test endpoint triggered")
    if not param:
        return create_response(
            request,
            status_code=400,
            error="No param provided. Use ?param=value",
        )
    return create_response(
        request,
        status_code=200,
        data={"param": param},
    )


class ChatService:
    """
    로컬 테스트 전용으로 슬림화된 채팅 서비스.
    - RAG MongoDB(동기 PyMongo)만 연결
    - 로그/대화 DB, 인증, 스트리밍 관련 코드는 제거됨
    """

    def __init__(self):
        self.model = None
        self.collection = None
        self.rag_client = None
        self.translate_model = None
        self.query_rewriter = None
        self.config = None
        self.rag_db = None

    def initialize(self):
        """모델, RAG DB 설정 초기화."""
        logging.info("====== Application initialization started ======")

        config_path = os.path.join(
            os.path.dirname(__file__), "configs", "config.json"
        )
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = json.load(f)
            self.db_name = self.config["path"]["db_name"]
            self.collection_name = self.config["path"]["collection_name"]

        from query_model import ChatModel

        self.model = ChatModel(self.config)
        self.translate_model = TranslateModel(self.config)
        self.query_rewriter = QueryRewriter(self.config)

        rag_mongodb_uri = os.getenv("RAG_DATA_MONGODB_URI")
        openai_api_key = os.getenv("OPENAI_API_KEY")

        if not rag_mongodb_uri or not openai_api_key:
            raise ValueError(
                "Required environment variables (RAG_DATA_MONGODB_URI, "
                "OPENAI_API_KEY) are not set"
            )

        logging.info("model and environment variables initialized")

        self.rag_client = MongoClient(
            rag_mongodb_uri,
            maxPoolSize=50,
            minPoolSize=10,
            maxIdleTimeMS=45000,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=10000,
        )
        self.rag_db = self.rag_client[self.db_name]
        self.collection = self.rag_db[self.collection_name]

        init_chat_db()

        logging.info("RAG database and chat log DB initialized successfully")

    def get_query_model_response_with_docs(
        self,
        conversation_history,
        query_text,
        mongo_query=None,
        query_lang=None,
    ):
        """하이브리드 검색 + LLM 응답 생성. 검색 문서 ID도 함께 반환."""
        response = self.model.generate_ai_response(
            conversation_history,
            query_text,
            self.collection,
            mongo_query=mongo_query,
            query_lang=query_lang,
        )

        return {
            "answer": response["answer"],
            "retrieved_doc_ids": response["retrieved_doc_ids"],
            "retrieved_docs": response["retrieved_docs"],
        }


def init_chat_db() -> None:
    """SQLite 메시지 테이블 초기화 (idempotent)."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                sender      TEXT NOT NULL CHECK(sender IN ('user','bot')),
                content     TEXT NOT NULL,
                ts          TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_session_id "
            "ON messages(session_id, id)"
        )


def load_history(session_id: str, limit: int = HISTORY_LIMIT) -> list[dict]:
    """세션의 최근 N개 메시지를 시간 순서대로 반환."""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT sender, content FROM messages "
            "WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    rows.reverse()
    return [
        {"speaker": "human" if sender == "user" else "ai", "utterance": content}
        for sender, content in rows
    ]


def save_turn(session_id: str, user_msg: str, bot_msg: str) -> None:
    """user 메시지와 bot 응답을 한 트랜잭션으로 저장."""
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany(
                "INSERT INTO messages (session_id, sender, content, ts) "
                "VALUES (?, ?, ?, ?)",
                [
                    (session_id, "user", user_msg, now),
                    (session_id, "bot", bot_msg, now),
                ],
            )
    except Exception as exc:
        logging.error("Failed to save turn for session %s: %s", session_id, exc)


@app.post("/api/question")
async def question(request: Request):
    """
    로컬 테스트용 단발성 채팅 엔드포인트.
    인증/히스토리 없이 번역 → 하이브리드 RAG → LLM 응답을 한 번에 반환.
    """
    logging.info("Question endpoint triggered")

    try:
        req_body = await request.json()
    except ValueError as e:
        return create_response(
            request,
            status_code=400,
            error="잘못된 JSON 형식입니다.",
            details={"errorType": "ValueError", "errorMessage": str(e)},
        )

    query = req_body.get("query", "")
    if not query or not str(query).strip():
        return create_response(
            request,
            status_code=400,
            error="query 필드가 필요합니다.",
            details={"field": "query", "issue": "Missing or empty query"},
        )

    session_id = req_body.get("sessionId", "")
    if not session_id or not str(session_id).strip():
        return create_response(
            request,
            status_code=400,
            error="sessionId 필드가 필요합니다.",
            details={"field": "sessionId", "issue": "Missing or empty sessionId"},
        )
    session_id = str(session_id).strip()

    logging.info("[question] sessionId=%s query=%s", session_id, query)

    try:
        chat_service = await get_chat_service()
    except Exception as exc:
        logging.exception("Failed to initialize chat service")
        return create_response(
            request,
            status_code=500,
            error="채팅 서비스 초기화에 실패했습니다.",
            details={"errorType": type(exc).__name__, "errorMessage": str(exc)},
        )

    try:
        conversation_history = load_history(session_id)
    except Exception as exc:
        logging.error("Failed to load history: %s", exc, exc_info=True)
        return create_response(
            request,
            status_code=500,
            error="대화 기록 조회 중 오류가 발생했습니다.",
            details={"errorType": type(exc).__name__, "errorMessage": str(exc)},
        )

    logging.info(
        "[question] loaded %d prior turns for session %s",
        len(conversation_history),
        session_id,
    )

    rewritten_query = chat_service.query_rewriter.rewrite(conversation_history, query)
    if rewritten_query != query:
        logging.info("[question] rewrote query: %r -> %r", query, rewritten_query)

    try:
        translation = chat_service.translate_model.translate_query(rewritten_query)
    except Exception as exc:
        logging.error("Translation failed: %s", exc, exc_info=True)
        return create_response(
            request,
            status_code=500,
            error="질문 번역 처리에 실패했습니다.",
            details={"errorType": type(exc).__name__, "errorMessage": str(exc)},
        )

    translated_query = translation["translated_query"]
    query_lang = translation.get("query_lang")
    mongo_query = translation.get("mongo_query") or []

    if "mongo_query" in req_body:
        try:
            mongo_query = parse_mongo_query(req_body.get("mongo_query"))
        except Exception as exc:
            return create_response(
                request,
                status_code=400,
                error="잘못된 MongoDB 쿼리 형식입니다.",
                details={
                    "field": "mongo_query",
                    "errorType": type(exc).__name__,
                    "errorMessage": str(exc),
                },
            )

    logging.info(
        "[question] translated=%s lang=%s pipeline=%s",
        translated_query,
        query_lang,
        json.dumps(mongo_query, ensure_ascii=False),
    )

    try:
        result = chat_service.get_query_model_response_with_docs(
            conversation_history=conversation_history,
            query_text=translated_query,
            mongo_query=mongo_query,
            query_lang=query_lang,
        )
    except Exception as exc:
        logging.error("RAG response generation failed: %s", exc, exc_info=True)
        return create_response(
            request,
            status_code=500,
            error="응답 생성 중 오류가 발생했습니다.",
            details={"errorType": type(exc).__name__, "errorMessage": str(exc)},
        )

    answer = result["answer"]
    retrieved_doc_ids = result["retrieved_doc_ids"]

    save_turn(session_id, query, answer)

    return create_response(
        request,
        status_code=200,
        data={
            "sessionId": session_id,
            "answer": answer,
            "rewrittenQuery": rewritten_query,
            "translatedQuery": translated_query,
            "queryLang": query_lang,
            "retrievedDocIds": retrieved_doc_ids,
        },
    )
