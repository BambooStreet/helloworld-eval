import os
import json
import logging
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

load_dotenv()


# Ensure INFO logs are emitted when running locally
logger = logging.getLogger()
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)
logger.setLevel(logging.INFO)

app = FastAPI()

_chat_service = None
_service_lock = threading.Lock()

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
LOG_FILE = os.path.join(LOG_DIR, "chat_test.jsonl")


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

        logging.info("RAG database initialized successfully")

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


def append_local_log(entry: dict) -> None:
    """로컬 JSONL 파일에 한 줄 append."""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as log_err:
        logging.error("Failed to write local log: %s", log_err)


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

    logging.info("[question] Incoming query=%s", query)

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
        translation = chat_service.translate_model.translate_query(query)
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
            conversation_history=[],
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

    append_local_log(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "query": query,
            "translatedQuery": translated_query,
            "queryLang": query_lang,
            "mongoQuery": mongo_query,
            "retrievedDocIds": retrieved_doc_ids,
            "answer": answer,
        }
    )

    return create_response(
        request,
        status_code=200,
        data={
            "answer": answer,
            "translatedQuery": translated_query,
            "queryLang": query_lang,
            "retrievedDocIds": retrieved_doc_ids,
        },
    )
