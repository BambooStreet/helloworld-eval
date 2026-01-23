import os
import json
import logging
import time
import functools
import asyncio
import threading
import uuid
from datetime import datetime, timezone
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from openai import AsyncOpenAI
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient
from bson import ObjectId

from utils import parse_mongo_query
from translate_model import TranslateModel


# Ensure INFO logs are emitted when running locally
logger = logging.getLogger()
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)
logger.setLevel(logging.INFO)

app = FastAPI()

# 전역 변수 선언
_chat_service = None
_service_lock = threading.Lock()
_openai_client = None

# OpenAI 동시 요청 제어를 위한 세마포어
# 동시에 최대 100개 요청까지 허용
_openai_semaphore = asyncio.Semaphore(100)


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


async def get_openai_client():
    """비동기 OpenAI 클라이언트 싱글톤"""
    global _openai_client
    if _openai_client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable is not set")
        _openai_client = AsyncOpenAI(api_key=api_key)
    return _openai_client


async def openai_request_with_limit(coroutine):
    """
    OpenAI API 호출을 세마포어와 함께 실행하고 에러 발생 시 상세 정보 반환

    Parameters:
        coroutine: 실행할 비동기 함수

    Returns:
        API 응답

    Raises:
        Exception: 에러 발생 시 상세 정보와 함께 발생
    """
    async with _openai_semaphore:
        try:
            result = await coroutine
            return result
        except Exception as e:
            error_str = str(e)
            error_type = type(e).__name__

            # 에러 상세 정보 로깅
            logging.error(
                f"[OpenAI Error] Type: {error_type}, Message: {error_str}",
                exc_info=True,
            )

            # 에러 타입별 메시지 분류
            if "429" in error_str or "rate_limit" in error_str.lower():
                error_details = {
                    "errorType": "RATE_LIMIT_EXCEEDED",
                    "errorCode": "429",
                    "originalError": error_str,
                }
                raise Exception(f"OpenAI Rate Limit: {error_details}")
            if "401" in error_str or "unauthorized" in error_str.lower():
                error_details = {
                    "errorType": "AUTHENTICATION_ERROR",
                    "errorCode": "401",
                    "originalError": error_str,
                }
                raise Exception(f"OpenAI Authentication Error: {error_details}")
            if "400" in error_str or "bad request" in error_str.lower():
                error_details = {
                    "errorType": "BAD_REQUEST",
                    "errorCode": "400",
                    "originalError": error_str,
                }
                raise Exception(f"OpenAI Bad Request: {error_details}")
            error_details = {
                "errorType": error_type,
                "originalError": error_str,
            }
            raise Exception(f"OpenAI API Error: {error_details}")


async def get_chat_service():
    """비동기 ChatService 싱글톤 (thread-safe)"""
    global _chat_service
    if _chat_service is None:
        with _service_lock:
            if _chat_service is None:
                _chat_service = ChatService()
                await _chat_service.initialize()
    return _chat_service


def extract_user_id_from_token(auth_header: str) -> int:
    """
    JWT 토큰에서 사용자 ID를 추출합니다.

    Parameters:
        auth_header: Authorization 헤더 값 (Bearer <token> 형식)

    Returns:
        int: 사용자 ID
    """
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        token = auth_header

    import jwt

    secret_key = os.getenv("JWT_SECRET_KEY")
    if not secret_key:
        raise ValueError("JWT_SECRET_KEY environment variable is not set")
    
    decoded = jwt.decode(token, secret_key, algorithms=["HS256"])
    return decoded.get("id")


def require_auth(func_handler):
    """
    인증이 필요한 엔드포인트에 적용하는 데코레이터 (비동기 지원)
    JWT 토큰을 검증하고 user_id를 request.state에 주입합니다.
    """

    @functools.wraps(func_handler)
    async def async_wrapper(request: Request, *args, **kwargs):
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return create_response(
                request,
                status_code=401,
                error="Authorization header is required",
            )

        try:
            user_id = extract_user_id_from_token(auth_header)
            request.state.user_id = user_id
            logging.info(f"[{func_handler.__name__}] User ID: {user_id}")
        except Exception as e:
            logging.error(f"Failed to extract user ID from token: {e}")
            return create_response(
                request,
                status_code=401,
                error="Invalid authorization token",
                details={"errorType": type(e).__name__, "errorMessage": str(e)},
            )

        return await func_handler(request, *args, **kwargs)

    return async_wrapper


# 테스트 엔드포인트 (항상 동작 보장)
@app.get("/api/get_echo_call")
async def get_echo_call(request: Request, param: str | None = None):
    """
    테스트용 엔드포인트입니다.

    Parameters:
        request (Request): HTTP 요청 객체
        쿼리 파라미터 'param'을 통해 값을 전달받음

    Returns:
        JSONResponse: 입력받은 파라미터를 그대로 반환

    사용법:
        GET /api/get_echo_call?param=hello
    """

    logging.info("Test endpoint triggered")

    try:
        logging.info(f"Received param: {param}")

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
    except Exception as e:
        logging.error(f"Error in test endpoint: {str(e)}")
        return create_response(
            request,
            status_code=500,
            error=str(e),
            details={"errorType": type(e).__name__},
        )


class ChatService:
    """
    핵심 AI 채팅 서비스를 구현한 클래스입니다. (비동기 처리 지원)

    동시성 처리:
    - MongoDB: Motor 비동기 드라이버 사용
      * Document 레벨 Lock은 MongoDB가 내부적으로 자동 처리
      * 별도의 애플리케이션 레벨 Lock 불필요
      * 연결 풀 최적화로 수천 개 동시 요청 처리 가능

    - OpenAI: 429 에러 핸들링
      * Semaphore로 동시 요청 수 제한 (100개)
      * 429 에러 발생 시 자동 재시도 (지수 백오프)
      * 수동 Rate Limit 카운팅 없음
    """

    def __init__(self):
        self.model = None
        self.collection = None
        self.collection_sync = None  # 동기 컬렉션 (스트리밍용)
        self.rag_client = None
        self.rag_client_sync = None  # 동기 클라이언트 (스트리밍용)
        self.log_client = None
        self.translate_model = None
        self.config = None
        self.rag_db = None
        self.log_db = None
        self.chat_collection = None
        self.rooms_collection = None

    async def initialize(self):
        """
        이 함수는 모델, DB 설정을 초기화하고, 환경 변수를 설정하는 비동기 함수입니다.

        MongoDB 연결 풀 설정:
        - maxPoolSize=50: 최대 50개 동시 연결
        - minPoolSize=10: 최소 10개 연결 유지
        - 타임아웃 최적화로 빠른 응답 보장

        Returns:
            dict: 설정 정보가 담긴 딕셔너리
        """
        logging.info("====== Application initialization started ======")

        try:
            config_path = os.path.join(
                os.path.dirname(__file__), "configs", "config.json"
            )
            with open(config_path, "r", encoding="utf-8") as f:
                self.config = json.load(f)
                self.db_name = self.config["path"]["db_name"]
                self.collection_name = self.config["path"]["collection_name"]
        except FileNotFoundError as e:
            logging.error(f"Config file not found: {e}")
            raise
        except json.JSONDecodeError as e:
            logging.error(f"Config JSON parse error: {e}")
            raise

        # Lazy import with error handling
        try:
            from query_model import ChatModel

            self.model = ChatModel(self.config)
        except ImportError as e:
            logging.error(f"Failed to import ChatModel: {e}")
            raise
        except Exception as e:
            logging.error(f"Failed to initialize ChatModel: {e}")
            raise

        try:
            self.translate_model = TranslateModel(self.config)
        except Exception as e:
            logging.error(f"Failed to initialize TranslateModel: {e}")
            raise

        # 환경변수 직접 가져오기
        rag_mongodb_uri = os.getenv("RAG_DATA_MONGODB_URI")
        log_mongodb_uri = os.getenv("LOG_DATA_MONGODB_URI")
        openai_api_key = os.getenv("OPENAI_API_KEY")

        if not rag_mongodb_uri or not log_mongodb_uri or not openai_api_key:
            raise ValueError(
                "Required environment variables (RAG_DATA_MONGODB_URI, "
                "LOG_DATA_MONGODB_URI, OPENAI_API_KEY) are not set"
            )

        logging.info("model and environment variables initialized")

        try:
            # RAG 데이터베이스 (검색/지식) 클라이언트 - 비동기
            self.rag_client = AsyncIOMotorClient(
                rag_mongodb_uri,
                maxPoolSize=50,
                minPoolSize=10,
                maxIdleTimeMS=45000,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=10000,
            )
            # RAG 데이터베이스 - 동기 클라이언트 (스트리밍용)
            self.rag_client_sync = MongoClient(
                rag_mongodb_uri,
                maxPoolSize=50,
                minPoolSize=10,
                maxIdleTimeMS=45000,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=10000,
            )
            # 로그/대화 데이터베이스 클라이언트
            self.log_client = AsyncIOMotorClient(
                log_mongodb_uri,
                maxPoolSize=50,
                minPoolSize=10,
                maxIdleTimeMS=45000,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=10000,
            )

            logging.info("MongoDB async clients initialized (RAG + LOG)")

            # RAG DB/컬렉션 설정 (비동기)
            self.rag_db = self.rag_client[self.db_name]
            self.collection = self.rag_db[self.collection_name]
            # RAG DB/컬렉션 설정 (동기 - 스트리밍용)
            rag_db_sync = self.rag_client_sync[self.db_name]
            self.collection_sync = rag_db_sync[self.collection_name]
            # 로그 DB/컬렉션 설정
            self.log_db = self.log_client["chatdb"]
            self.chat_collection = self.log_db["chat"]
            self.rooms_collection = self.log_db["rooms"]

            logging.info("databases initialized successfully")
        except Exception as e:
            logging.error(f"Error loading database: {str(e)}")
            raise

    def get_query_model_response_with_docs(
        self,
        conversation_history,
        query_text,
        mongo_query=None,
        query_lang=None,
    ):
        """
        키워드 기반 하이브리드 검색 모델로부터 답변을 생성하고 검색된 문서들의 인덱스를 반환
        """
        try:
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

        except Exception as e:
            logging.error("Error retrieving documents: %s", e)
            return {
                "answer": "ERROR",
                "retrieved_doc_ids": [],
                "retrieved_docs": [],
            }

    def stream_query_model_response_with_docs(
        self, conversation_history, query_text, mongo_query=None, query_lang=None
    ):
        """스트리밍 응답을 생성하는 제너레이터. (동기 컬렉션 사용)"""

        try:
            yield from self.model.generate_ai_response_stream(
                conversation_history,
                query_text,
                self.collection_sync,  # 동기 컬렉션 사용
                mongo_query=mongo_query,
                query_lang=query_lang,
            )
        except Exception as exc:
            logging.error("Error during streaming response: %s", exc)
            raise

    async def save_chat_log(self, user_id: int, room_id: str, question: str, answer: str):
        """
        채팅 로그를 데이터베이스에 저장합니다. (비동기)

        동시성 처리:
        - MongoDB는 Document 레벨에서 자동으로 Lock을 관리
        - 여러 요청이 동시에 같은 room_id로 저장해도 안전
        - insert_one과 update_one은 Atomic 연산

        Parameters:
            user_id: 사용자 ID
            room_id: 대화방 ID
            question: 사용자 질문
            answer: AI 답변
        """
        try:
            # 사용자 메시지 저장
            await self.chat_collection.insert_one(
                {
                    "roomId": room_id,
                    "sender": "user",
                    "content": question,
                    "time": datetime.utcnow(),
                    "_class": "Helloworld.helloworld_webflux.domain.ChatMessage",
                }
            )

            # AI 응답 저장
            await self.chat_collection.insert_one(
                {
                    "roomId": room_id,
                    "sender": "bot",
                    "content": answer,
                    "time": datetime.utcnow(),
                    "_class": "Helloworld.helloworld_webflux.domain.ChatMessage",
                }
            )

            # rooms 컬렉션 updatedAt 업데이트
            await self.rooms_collection.update_one(
                {"_id": ObjectId(room_id)},
                {"$set": {"updatedAt": datetime.utcnow()}},
            )

            logging.info(f"Chat log saved for room {room_id}")
        except Exception as e:
            logging.error(f"Error saving chat log: {e}")

    async def get_recent_room_and_logs(self, user_id: int):
        """
        사용자의 가장 최근 대화방과 로그를 조회합니다. (비동기)

        Parameters:
            user_id: 사용자 ID

        Returns:
            dict: {"roomId": str, "chatLogs": [{"content": str, "sender": str}]}
        """
        try:
            recent_room = await self.rooms_collection.find_one(
                {"userId": user_id},
                sort=[("updatedAt", -1)],
            )

            if not recent_room:
                return {"roomId": None, "chatLogs": []}

            room_id = str(recent_room["_id"])
            return await self.get_room_logs(room_id)
        except Exception as e:
            logging.error(f"Error fetching recent room: {e}")
            return {"roomId": None, "chatLogs": []}

    async def get_room_logs(self, room_id: str, user_id: int = None):
        """
        특정 대화방의 로그를 조회합니다. (비동기)

        Parameters:
            room_id: 대화방 ID
            user_id: 사용자 ID (권한 검증용, 선택)

        Returns:
            dict: {"roomId": str, "chatLogs": [{"content": str, "sender": str}]}
            권한이 없으면 None 반환
        """
        try:
            if user_id is not None:
                room = await self.rooms_collection.find_one({"_id": ObjectId(room_id)})
                if not room:
                    logging.warning(f"Room {room_id} not found")
                    return None
                if room.get("userId") != user_id:
                    logging.warning(f"User {user_id} has no access to room {room_id}")
                    return None

            cursor = self.chat_collection.find({"roomId": room_id}).sort("time", 1)

            logs = await cursor.to_list(length=None)

            chat_logs = []
            for log in logs:
                chat_logs.append({"content": log["content"], "sender": log["sender"]})

            return {"roomId": room_id, "chatLogs": chat_logs}
        except Exception as e:
            logging.error(f"Error fetching room logs: {e}")
            return {"roomId": room_id, "chatLogs": []}

# question_stream -> chat/ask
# 대화 기록 기반의 List[str] 입력받기
@app.post("/api/chat/ask")
@require_auth
async def chat_ask(request: Request):
    """
    SSE(Server-Sent Events) 기반의 실시간 스트리밍 응답 API
    """
    logging.info("Chat ask function triggered.")

    user_id = request.state.user_id

    room_id = request.query_params.get("roomId")
    if not room_id:
        return create_response(
            request,
            status_code=400,
            error="roomId 파라미터가 필요합니다.",
            details={"field": "roomId", "issue": "Missing required parameter"},
        )

    # roomId 유효성 검사 (MongoDB ObjectId 형식)
    if not ObjectId.is_valid(room_id):
        return create_response(
            request,
            status_code=400,
            error="유효하지 않은 roomId 형식입니다.",
            details={
                "field": "roomId",
                "value": room_id,
                "issue": "roomId must be a 24-character hex string",
            },
        )

    try:
        req_body = await request.json()
    except ValueError as e:
        return create_response(
            request,
            status_code=400,
            error="잘못된 JSON 형식입니다.",
            details={"errorType": "ValueError", "errorMessage": str(e)},
        )

    explicit_query = req_body.get("query", "")
    if not explicit_query or str(explicit_query).strip() == "":
        return create_response(
            request,
            status_code=400,
            error="query 필드가 필요합니다.",
            details={"field": "query", "issue": "Missing or empty query"},
        )

    logging.info("[chat_ask] Incoming query=%s, roomId=%s", explicit_query, room_id)

    processing_start = time.perf_counter()

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
        room_logs = await chat_service.get_room_logs(room_id, user_id)
        if room_logs is None:
            return create_response(
                request,
                status_code=403,
                error="해당 대화방에 접근 권한이 없거나 대화방을 찾을 수 없습니다.",
                details={"roomId": room_id, "issue": "Access denied or room not found"},
            )

        chat_logs = room_logs.get("chatLogs", [])
        conversation = []

        recent_logs = chat_logs[-10:] if len(chat_logs) > 10 else chat_logs

        for log in recent_logs:
            speaker = "human" if log["sender"] == "user" else "ai"
            conversation.append({"speaker": speaker, "utterance": log["content"]})

        logging.info(
            "[chat_ask] Loaded %d conversation turns from room %s",
            len(conversation),
            room_id,
        )
    except Exception as exc:
        logging.error("Failed to load room logs: %s", exc, exc_info=True)
        return create_response(
            request,
            status_code=500,
            error="대화방 로그 로드에 실패했습니다.",
            details={"errorType": type(exc).__name__, "errorMessage": str(exc)},
        )

    translate_start = time.perf_counter()
    try:
        translation_result = chat_service.translate_model.translate_query(explicit_query)
    except Exception as exc:
        logging.error("Translation failed: %s", exc, exc_info=True)
        return create_response(
            request,
            status_code=500,
            error="질문 번역 처리에 실패했습니다.",
            details={"errorType": type(exc).__name__, "errorMessage": str(exc)},
        )

    translated_query = translation_result["translated_query"]
    query_lang = translation_result.get("query_lang")
    mongo_query = translation_result.get("mongo_query") or []

    translate_duration = time.perf_counter() - translate_start
    logging.info("[Translate] Completed in %.2f s (lang=%s)", translate_duration, query_lang)

    logging.info(
        "[chat_ask] Translation result lang=%s translated=%s pipeline=%s",
        query_lang,
        translated_query,
        json.dumps(mongo_query, ensure_ascii=False),
    )

    if "mongo_query" in req_body:
        try:
            mongo_query = parse_mongo_query(req_body.get("mongo_query"))
        except Exception as exc:
            logging.error("Invalid mongo_query provided: %s", exc, exc_info=True)
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

    augmented_conversation = list(conversation)
    augmented_conversation.append({"speaker": "human", "utterance": translated_query})

    def stream_response():
        aggregated_answer = []
        try:
            streaming_generator = chat_service.stream_query_model_response_with_docs(
                augmented_conversation,
                translated_query,
                mongo_query=mongo_query,
                query_lang=query_lang,
            )

            for chunk in streaming_generator:
                if chunk.get("type") == "metadata":
                    logging.info(
                        "[chat_ask] Streaming metadata=%s",
                        json.dumps(chunk, ensure_ascii=False),
                    )
                elif chunk.get("type") == "content" and chunk.get("content"):
                    aggregated_answer.append(chunk.get("content"))
                payload = json.dumps(chunk, ensure_ascii=False)
                yield f"data: {payload}\n\n"
        except Exception as exc:
            logging.error("Streaming error: %s", exc, exc_info=True)
            request_id = (
                request.headers.get("x-request-id")
                or request.headers.get("x-ms-request-id")
                or str(uuid.uuid4())
            )
            error_response = {
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "path": str(request.url),
                "status": 500,
                "error": "스트리밍 중 오류가 발생했습니다.",
                "requestId": request_id,
                "details": {"errorType": type(exc).__name__, "errorMessage": str(exc)},
            }
            yield f"data: {json.dumps(error_response, ensure_ascii=False)}\n\n"
        finally:
            elapsed_seconds = time.perf_counter() - processing_start
            final_answer = "".join(aggregated_answer)
            logging.info("[Answer] Final streamed answer=%s", final_answer)
            logging.info("[Total] Request completed in %.2f s", elapsed_seconds)

            # 동기 컨텍스트에서 비동기 태스크 실행
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(
                    chat_service.save_chat_log(
                        user_id, room_id, explicit_query, final_answer
                    )
                )
            except RuntimeError:
                # 이벤트 루프가 없으면 새로 생성해서 실행
                asyncio.run(
                    chat_service.save_chat_log(
                        user_id, room_id, explicit_query, final_answer
                    )
                )

    try:
        return StreamingResponse(
            stream_response(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except Exception as exc:
        logging.exception("Question streaming handler failed")
        return create_response(
            request,
            status_code=500,
            error="스트리밍 응답 처리 중 오류가 발생했습니다.",
            details={"errorType": type(exc).__name__, "errorMessage": str(exc)},
        )


@app.post("/api/summary")
@require_auth
async def summary(request: Request):
    """
    채팅방의 대화 로그를 바탕으로 요약문을 생성하는 엔드포인트
    """
    logging.info("Summary function triggered.")

    user_id = request.state.user_id
    _ = user_id

    room_id = request.query_params.get("roomId")
    if not room_id:
        return create_response(
            request,
            status_code=400,
            error="roomId 파라미터가 필요합니다.",
            details={"field": "roomId", "issue": "roomId parameter is required"},
        )

    try:
        chat_service = await get_chat_service()

        room_logs = await chat_service.get_room_logs(room_id)

        if not room_logs["chatLogs"]:
            return create_response(
                request,
                status_code=200,
                data={"summary": None, "message": f"대화방 {room_id}에 로그가 없습니다."},
            )

        conversation_text = ""
        for log in room_logs["chatLogs"]:
            sender = "사용자" if log["sender"] == "user" else "AI"
            conversation_text += f"{sender}: {log['content']}\n\n"

        from prompts import prompts

        summary_prompt_template = prompts.load_prompt("chat_summary")
        summary_prompt = summary_prompt_template.format(conversation=conversation_text)

        client = await get_openai_client()
        response = await openai_request_with_limit(
            client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": "당신은 채팅 대화 내용을 요약하는 전문 어시스턴트입니다.",
                    },
                    {"role": "user", "content": summary_prompt},
                ],
                temperature=0.3,
                max_tokens=300,
            )
        )

        summary_text = response.choices[0].message.content.strip()

        return create_response(
            request,
            status_code=200,
            data={"summary": summary_text},
        )

    except Exception as e:
        logging.exception("Summary handler failed")
        return create_response(
            request,
            status_code=500,
            error="요약 생성 중 오류가 발생했습니다.",
            details={"errorType": type(e).__name__, "errorMessage": str(e)},
        )


@app.get("/api/chat/room-log")
@require_auth
async def chat_room_log(request: Request):
    """
    특정 대화방의 로그를 반환하는 엔드포인트
    """
    logging.info("Chat room log function triggered.")

    user_id = request.state.user_id

    room_id = request.query_params.get("roomId")
    if not room_id:
        return create_response(
            request,
            status_code=400,
            error="roomId 파라미터가 필요합니다.",
            details={"field": "roomId", "issue": "roomId parameter is required"},
        )

    try:
        chat_service = await get_chat_service()

        response_data = await chat_service.get_room_logs(room_id, user_id)

        if response_data is None:
            return create_response(
                request,
                status_code=403,
                error="해당 대화방에 접근 권한이 없거나 대화방을 찾을 수 없습니다.",
                details={"roomId": room_id, "issue": "Access denied or room not found"},
            )

        return create_response(
            request,
            status_code=200,
            data=response_data,
        )
    except Exception as e:
        logging.exception("Chat room log handler failed")
        return create_response(
            request,
            status_code=500,
            error="대화방 로그 조회 중 오류가 발생했습니다.",
            details={"errorType": type(e).__name__, "errorMessage": str(e)},
        )


@app.get("/api/chat/recent-room")
@require_auth
async def chat_recent_room(request: Request):
    """
    가장 최근 대화방 기록을 반환하는 엔드포인트
    """
    logging.info("Chat recent room function triggered.")

    user_id = request.state.user_id

    try:
        chat_service = await get_chat_service()

        response_data = await chat_service.get_recent_room_and_logs(user_id)

        return create_response(
            request,
            status_code=200,
            data=response_data,
        )
    except Exception as e:
        logging.exception("Chat recent room handler failed")
        return create_response(
            request,
            status_code=500,
            error="최근 대화방 조회 중 오류가 발생했습니다.",
            details={"errorType": type(e).__name__, "errorMessage": str(e)},
        )


@app.post("/api/cv_generation")
@require_auth
async def cv_generation(request: Request):
    """
    이력서 생성 요청을 처리하는 엔드포인트입니다.
    """

    logging.info("CV Generation function triggered.")

    user_id = request.state.user_id
    _ = user_id

    try:
        from prompts import prompts

        openai_api_key = os.getenv("OPENAI_API_KEY")
        if not openai_api_key:
            raise ValueError("OPENAI_API_KEY environment variable is not set")

        req_body = await request.json()

        if not all(field in req_body for field in ["personal", "experience", "language"]):
            missing_fields = [
                f
                for f in ["personal", "experience", "language"]
                if f not in req_body
            ]
            return create_response(
                request,
                status_code=400,
                error="필수 필드가 누락되었습니다.",
                details={"missingFields": missing_fields, "issue": "Missing required fields"},
            )

        system_prompt = prompts.load_prompt("cv")

        foreign_languages = "\n\t".join(
            [
                f"{lang['name']}: {lang['level']}"
                for lang in req_body["language"]["others"]
            ]
        )

        experiences = "\n".join([f"- {exp['work']}" for exp in req_body["experience"]])

        user_prompt = f"""
        [인적사항]
        - 이름: {req_body['personal']['name']}
        - 국적: {req_body['personal']['nationality']}
        - 비자: {req_body['personal']['visa']}

        [근무경험]
        {experiences}

        [언어]
        - 한국어 수준: {req_body['language']['korean']}
        - 기타 외국어 수준
            {foreign_languages}

        [업무스킬]
        {', '.join(req_body['skills'])}

        [강점]
        {', '.join(req_body['strengths'])}

        [희망직무]
        {req_body.get('desired_position', '상관없음')}
        """
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = llm.invoke(input=messages)

        content = response.content
        intro_start = content.find("<자기소개문장>") + len("<자기소개문장>")
        intro_end = content.find("</자기소개문장>")
        details_start = content.find("<상세소개서>") + len("<상세소개서>")
        details_end = content.find("</상세소개서>")

        introduction = content[intro_start:intro_end].strip()
        details = content[details_start:details_end].strip()

        return create_response(
            request,
            status_code=200,
            data={"resume": {"introduction": introduction, "details": details}},
        )

    except ValueError as ve:
        logging.exception("CV generation configuration error")
        return create_response(
            request,
            status_code=500,
            error="서버 구성 오류가 발생했습니다.",
            details={"errorType": "ValueError", "errorMessage": str(ve)},
        )
    except Exception as e:
        logging.exception("CV generation handler failed on import/exec")
        return create_response(
            request,
            status_code=500,
            error="이력서 생성 중 오류가 발생했습니다.",
            details={"errorType": type(e).__name__, "errorMessage": str(e)},
        )
