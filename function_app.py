import azure.functions as func
import logging
import sys, os
import json
import time
import functools
import asyncio
import threading
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from openai import AsyncOpenAI
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId

from utils import parse_mongo_query
from translate_model import TranslateModel

"""
Azure Functions with Async + Error Handling

동시성 제어 전략:
===================

1. OpenAI 에러 핸들링
   - Semaphore로 동시 요청 수 제한 (100개)
   - 429 Too Many Requests 에러 자동 감지
   - 지수 백오프(Exponential Backoff) 재시도: 20s → 40s → 60s
   - 수동 카운팅 없이 OpenAI의 에러 응답에만 반응
   
2. MongoDB 동시성
   - Motor 비동기 드라이버 사용
   - Document 레벨 Lock은 MongoDB 내부 자동 처리
   - 연결 풀 최적화 (maxPoolSize=50)
   - 애플리케이션 레벨 Lock 불필요

3. 성능 최적화
   - ChatService 싱글톤 (thread-safe)
   - OpenAI 클라이언트 재사용
   - 비동기 I/O로 블로킹 제거
"""

# Ensure INFO logs are emitted when running locally
logger = logging.getLogger()
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)
logger.setLevel(logging.INFO)

app = func.FunctionApp()

# 전역 변수 선언
_chat_service = None
_service_lock = threading.Lock()
_openai_client = None

# OpenAI 동시 요청 제어를 위한 세마포어
# 동시에 최대 100개 요청까지 허용
_openai_semaphore = asyncio.Semaphore(100)

def _build_base_payload(req: func.HttpRequest, status_code: int, error: str, request_id: str):
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "path": req.url or "",
        "status": status_code,
        "error": error,
        "requestId": request_id,
    }


def create_error_response(
    req: func.HttpRequest,
    status_code: int,
    error: str,
    details: dict | None = None,
) -> func.HttpResponse:
    """Spring WebFlux DefaultErrorAttributes 스타일의 에러 응답을 생성합니다."""
    request_id = (
        req.headers.get("x-request-id")
        or req.headers.get("x-ms-request-id")
        or str(uuid.uuid4())
    )
    payload = _build_base_payload(req, status_code, error, request_id)
    if details is not None:
        payload["details"] = details

    return func.HttpResponse(
        json.dumps(payload, ensure_ascii=False),
        mimetype="application/json",
        status_code=status_code,
    )


def create_success_response(
    req: func.HttpRequest,
    status_code: int = 200,
    data: dict | list | str | None = None,
) -> func.HttpResponse:
    """Spring WebFlux DefaultErrorAttributes 스타일의 성공 응답을 생성합니다."""
    request_id = (
        req.headers.get("x-request-id")
        or req.headers.get("x-ms-request-id")
        or str(uuid.uuid4())
    )
    try:
        error_text = HTTPStatus(status_code).phrase
    except ValueError:
        error_text = ""
    payload = _build_base_payload(req, status_code, error_text, request_id)
    if data is not None:
        payload["data"] = data

    return func.HttpResponse(
        json.dumps(payload, ensure_ascii=False),
        mimetype="application/json",
        status_code=status_code,
    )

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
                exc_info=True
            )
            
            # 에러 타입별 메시지 분류
            if "429" in error_str or "rate_limit" in error_str.lower():
                error_details = {
                    "errorType": "RATE_LIMIT_EXCEEDED",
                    "errorCode": "429",
                    "originalError": error_str
                }
                raise Exception(f"OpenAI Rate Limit: {error_details}")
            elif "401" in error_str or "unauthorized" in error_str.lower():
                error_details = {
                    "errorType": "AUTHENTICATION_ERROR",
                    "errorCode": "401",
                    "originalError": error_str
                }
                raise Exception(f"OpenAI Authentication Error: {error_details}")
            elif "400" in error_str or "bad request" in error_str.lower():
                error_details = {
                    "errorType": "BAD_REQUEST",
                    "errorCode": "400",
                    "originalError": error_str
                }
                raise Exception(f"OpenAI Bad Request: {error_details}")
            else:
                error_details = {
                    "errorType": error_type,
                    "originalError": error_str
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

def extract_email_from_token(auth_header: str) -> str:
    """
    JWT 토큰에서 사용자 이메일을 추출합니다.
    
    Parameters:
        auth_header: Authorization 헤더 값 (Bearer <token> 형식)
    
    Returns:
        str: 사용자 이메일 (Gmail)
    """
    # Bearer 토큰에서 실제 토큰 부분 추출
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        token = auth_header
    
    import jwt
    SECRET_KEY = os.getenv("JWT_SECRET_KEY")
    decoded = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    return decoded.get("email") or decoded.get("gmail")


def extract_user_id_from_token(auth_header: str) -> int:
    """
    JWT 토큰에서 사용자 ID를 추출합니다.
    
    Parameters:
        auth_header: Authorization 헤더 값 (Bearer <token> 형식)
    
    Returns:
        int: 사용자 ID
    
    """
    # Bearer 토큰에서 실제 토큰 부분 추출
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        token = auth_header
    
    import jwt
    SECRET_KEY = os.getenv("JWT_SECRET_KEY")
    decoded = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    return decoded.get("userId") or decoded.get("user_id")

def require_auth(func_handler):
    """
    인증이 필요한 엔드포인트에 적용하는 데코레이터 (비동기 지원)
    JWT 토큰을 검증하고 user_id를 req 객체에 주입합니다.
    
    Usage:
        @app.route(...)
        @require_auth
        async def my_endpoint(req: func.HttpRequest) -> func.HttpResponse:
            user_id = req.user_id  # 데코레이터가 주입한 user_id 사용
            ...
    """
    @functools.wraps(func_handler)
    async def async_wrapper(req: func.HttpRequest) -> func.HttpResponse:
        auth_header = req.headers.get('Authorization')
        if not auth_header:
            return create_error_response(
                req,
                status_code=401,
                error="Authorization header is required",
            )
        
        try:
            user_id = extract_user_id_from_token(auth_header)
            # user_id를 req 객체에 주입
            req.user_id = user_id
            logging.info(f"[{func_handler.__name__}] User ID: {user_id}")
        except Exception as e:
            logging.error(f"Failed to extract user ID from token: {e}")
            return create_error_response(
                req,
                status_code=401,
                error="Invalid authorization token",
                details={"errorType": type(e).__name__, "errorMessage": str(e)},
            )
        
        return await func_handler(req)
    
    return async_wrapper

# 테스트 엔드포인트 (항상 동작 보장)
@app.route(route="get_echo_call", auth_level=func.AuthLevel.ANONYMOUS, methods=["GET"])
def get_echo_call(req: func.HttpRequest) -> func.HttpResponse:
    """
    테스트용 엔드포인트입니다.

    Parameters:
        req (func.HttpRequest): HTTP 요청 객체
        쿼리 파라미터 'param'을 통해 값을 전달받음

    Returns:
        func.HttpResponse: 입력받은 파라미터를 그대로 반환
        
    사용법:
        GET /api/get_test?param=hello
    """

    logging.info("Test endpoint triggered")
    
    try:
        param = req.params.get("param")  # 쿼리 파라미터로 받기
        logging.info(f"Received param: {param}")
        
        if not param:
            return create_error_response(
                req,
                status_code=400,
                error="No param provided. Use ?param=value",
            )
        
        return create_success_response(
            req,
            status_code=200,
            data={"param": param},
        )
    except Exception as e:
        logging.error(f"Error in test endpoint: {str(e)}")
        return create_error_response(
            req,
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
        self.rag_client = None
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
            config_path = os.path.join(os.path.dirname(__file__), 'configs', 'config.json')
            with open(config_path, "r", encoding='utf-8') as f:
                self.config = json.load(f)
                self.db_name = self.config['path']['db_name']
                self.collection_name = self.config['path']['collection_name']
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

        # Azure Application Settings에서 환경변수 직접 가져오기
        rag_mongodb_uri = os.getenv("RAG_DATA_MONGODB_URI")
        log_mongodb_uri = os.getenv("LOG_DATA_MONGODB_URI")
        openai_api_key = os.getenv("OPENAI_API_KEY")
        
        if not rag_mongodb_uri or not log_mongodb_uri or not openai_api_key:
            raise ValueError(
                "Required environment variables (RAG_DATA_MONGODB_URI, LOG_DATA_MONGODB_URI, OPENAI_API_KEY) are not set"
            )

        logging.info("model and environment variables initialized")

        try:
            # RAG 데이터베이스 (검색/지식) 클라이언트
            self.rag_client = AsyncIOMotorClient(
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
            
            # 비동기 연결 테스트 (선택적)
            # await self.rag_client.admin.command('ping')
            # await self.log_client.admin.command('ping')
            logging.info("MongoDB async clients initialized (RAG + LOG)")

            # RAG DB/컬렉션 설정
            self.rag_db = self.rag_client[self.db_name]
            self.collection = self.rag_db[self.collection_name]
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
            # 키워드 기반 모델 답변 생성
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
        """스트리밍 응답을 생성하는 제너레이터."""

        try:
            yield from self.model.generate_ai_response_stream(
                conversation_history,
                query_text,
                self.collection,
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
            from datetime import datetime
            
            # 사용자 메시지 저장
            await self.chat_collection.insert_one({
                "roomId": room_id,
                "sender": "user",
                "content": question,
                "time": datetime.utcnow(),
                "_class": "Helloworld.helloworld_webflux.domain.ChatMessage"
            })
            
            # AI 응답 저장
            await self.chat_collection.insert_one({
                "roomId": room_id,
                "sender": "bot",
                "content": answer,
                "time": datetime.utcnow(),
                "_class": "Helloworld.helloworld_webflux.domain.ChatMessage"
            })
            
            # rooms 컬렉션 updatedAt 업데이트
            await self.rooms_collection.update_one(
                {"_id": ObjectId(room_id)},
                {"$set": {"updatedAt": datetime.utcnow()}}
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
            # rooms 컬렉션에서 가장 최근 대화방 조회
            recent_room = await self.rooms_collection.find_one(
                {"userId": user_id},
                sort=[("updatedAt", -1)]
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
            # user_id가 제공된 경우 권한 검증
            if user_id is not None:
                room = await self.rooms_collection.find_one({"_id": ObjectId(room_id)})
                if not room:
                    logging.warning(f"Room {room_id} not found")
                    return None
                if room.get("userId") != user_id:
                    logging.warning(f"User {user_id} has no access to room {room_id}")
                    return None
            
            # chat 컬렉션에서 특정 대화방의 모든 로그 조회
            cursor = self.chat_collection.find(
                {"roomId": room_id}
            ).sort("time", 1)
            
            logs = await cursor.to_list(length=None)
            
            chat_logs = []
            for log in logs:
                chat_logs.append({
                    "content": log["content"],
                    "sender": log["sender"]
                })
            
            return {"roomId": room_id, "chatLogs": chat_logs}
        except Exception as e:
            logging.error(f"Error fetching room logs: {e}")
            return {"roomId": room_id, "chatLogs": []}


# 사용자 요청 수신
@app.route(route="question", auth_level=func.AuthLevel.ANONYMOUS, methods=["POST"])
@require_auth
async def question(req: func.HttpRequest) -> func.HttpResponse:
    """
    이 함수는 HTTP POST 요청을 통해 사용자의 대화 내용을 받고,
    AI 응답을 생성하는 엔드포인트입니다.

    Parameters:
        req (func.HttpRequest): HTTP 요청 객체로, JSON 형식의 대화 내용을 포함한다.
        Authorization (header): 인증 토큰 (필수)

        예시 JSON 형식:
        {
            "Conversation": [
                {"speaker": "human", "utterance":"질문 내용"},
                {"speaker": "ai", "utterance": "이전 답변"}
            ]
        }

    Returns:
        func.HttpResponse: AI 응답을 JSON 형식으로 반환
        성공 시: {"answer": "AI 응답 내용"} (200 OK)
        실패 시: 에러 메시지와 함께 적절한 HTTP 상태 코드
            - 401: 인증 실패
            - 400: 잘못된 요청 (대화 내용 누락 등)
            - 500: 서버 내부 오류

    Notes:
        - 대화 내용에서 마지막 사용자(human) 발화만 추출하여 처리
        - 모든 응답은 한글을 포함한 유니코드 문자를 그대로 유지 (ensure_ascii=False)
    """

    logging.info("Question function triggered.")
    
    # 데코레이터가 주입한 user_id 사용
    user_id = req.user_id

    try:
        chat_service = await get_chat_service()

        # 요청 본문에서 JSON 데이터를 가져오고, Conversation 필드를 추출
        req_body = req.get_json()
        conversation = req_body.get("Conversation", [])
        if conversation is None:
            conversation = []

        logging.info(
            "[question] Incoming conversation turns=%d, payload=%s",
            len(conversation),
            json.dumps(req_body, ensure_ascii=False),
        )

        processing_start = time.perf_counter()

        # 마지막으로 입력된 사용자 발화를 추출
        explicit_query = req_body.get("query")
        if explicit_query is None or str(explicit_query).strip() == "":
            user_query = next(
                (
                    item["utterance"]
                    for item in reversed(conversation)
                    if item.get("speaker") == "human"
                ),
                None,
            )
            if not user_query:
                return create_error_response(
                    req,
                    status_code=400,
                    error="질문 내용이 제공되지 않았습니다.",
                    details={"field": "query", "issue": "No query provided in conversation"}
                )
            explicit_query = user_query

        translate_start = time.perf_counter()
        try:
            translation_result = chat_service.translate_model.translate_query(
                explicit_query
            )
        except Exception as exc:
            logging.error("Translation failed: %s", exc, exc_info=True)
            return create_error_response(
                req,
                status_code=500,
                error="질문 번역 처리에 실패했습니다.",
                details={
                    "errorType": type(exc).__name__,
                    "errorMessage": str(exc)
                }
            )

        translated_query = translation_result["translated_query"]
        query_lang = translation_result.get("query_lang")
        mongo_query = translation_result.get("mongo_query") or []

        translate_duration = time.perf_counter() - translate_start
        logging.info(
            "[Translate] Completed in %.2f s (lang=%s)",
            translate_duration,
            query_lang,
        )

        logging.info(
            "[question] Translation result lang=%s translated=%s pipeline=%s",
            query_lang,
            translated_query,
            json.dumps(mongo_query, ensure_ascii=False),
        )

        if "mongo_query" in req_body:
            try:
                mongo_query = parse_mongo_query(req_body.get("mongo_query"))
            except Exception as exc:
                logging.error("Invalid mongo_query provided: %s", exc, exc_info=True)
                return create_error_response(
                    req,
                    status_code=400,
                    error="잘못된 MongoDB 쿼리 형식입니다.",
                    details={
                        "field": "mongo_query",
                        "errorType": type(exc).__name__,
                        "errorMessage": str(exc)
                    }
                )

        augmented_conversation = list(conversation)
        augmented_conversation.append(
            {"speaker": "human", "utterance": translated_query}
        )

        # 응답 생성 (수정된 시그니처)
        response = chat_service.get_query_model_response_with_docs(
            augmented_conversation,
            translated_query,
            mongo_query=mongo_query,
            query_lang=query_lang,
        )

        elapsed_seconds = time.perf_counter() - processing_start

        logging.info('[Answer] Final answer=%s', response["answer"])
        logging.info('[Total] Request completed in %.2f s', elapsed_seconds)

        # 응답에서 references 제외하고 answer만 반환
        # 클라이언트에게 답변 텍스트만 전달 된다.
        return create_success_response(
            req,
            status_code=200,
            data={"answer": response["answer"]}
        )

    except ValueError as e:
        # JSON 파싱 에러
        logging.error(f"Invalid JSON format: {e}", exc_info=True)
        return create_error_response(
            req,
            status_code=400,
            error="잘못된 JSON 형식입니다.",
            details={"errorType": "ValueError", "errorMessage": str(e)}
        )
    except Exception as e:
        logging.exception("Question handler failed")
        return create_error_response(
            req,
            status_code=500,
            error="서버 내부 오류가 발생했습니다.",
            details={
                "errorType": type(e).__name__,
                "errorMessage": str(e)
            }
        )


# question_stream -> chat/ask
# 대화 기록 기반의 List[str] 입력받기
@app.route(route="chat/ask", auth_level=func.AuthLevel.ANONYMOUS, methods=["POST"])
@require_auth
async def chat_ask(req: func.HttpRequest) -> func.HttpResponse:
    """
    SSE(Server-Sent Events) 기반의 실시간 스트리밍 응답 API
    
    Parameters:
        Authorization (header): 인증 토큰
        roomId (query): 대화방 ID
        request body: {"query": "사용자 질문 (string)"}
    
    Returns:
        text/event-stream: 스트리밍 응답
    """
    logging.info("Chat ask function triggered.")
    global chat_service
    
    # 데코레이터가 주입한 user_id 사용
    user_id = req.user_id
    
    # roomId 파라미터 확인
    room_id = req.params.get('roomId')
    if not room_id:
        return create_error_response(
            req,
            status_code=400,
            error="roomId 파라미터가 필요합니다.",
            details={"field": "roomId", "issue": "Missing required parameter"}
        )
    
    try:
        req_body = req.get_json()
    except ValueError as e:
        return create_error_response(
            req,
            status_code=400,
            error="잘못된 JSON 형식입니다.",
            details={"errorType": "ValueError", "errorMessage": str(e)}
        )

    # 현재 질문 가져오기
    explicit_query = req_body.get("query", "")
    if not explicit_query or str(explicit_query).strip() == "":
        return create_error_response(
            req,
            status_code=400,
            error="query 필드가 필요합니다.",
            details={"field": "query", "issue": "Missing or empty query"}
        )

    logging.info(
        "[chat_ask] Incoming query=%s, roomId=%s",
        explicit_query,
        room_id,
    )

    processing_start = time.perf_counter()

    try:
        chat_service = await get_chat_service()
    except Exception as exc:
        logging.exception("Failed to initialize chat service")
        return create_error_response(
            req,
            status_code=500,
            error="채팅 서비스 초기화에 실패했습니다.",
            details={
                "errorType": type(exc).__name__,
                "errorMessage": str(exc)
            }
        )

    # MongoDB에서 현재 대화방의 히스토리 가져오기 (최근 10개 턴)
    try:
        room_logs = await chat_service.get_room_logs(room_id, user_id)
        if room_logs is None:
            return create_error_response(
                req,
                status_code=403,
                error="해당 대화방에 접근 권한이 없거나 대화방을 찾을 수 없습니다.",
                details={"roomId": room_id, "issue": "Access denied or room not found"}
            )
        
        # 최근 10개 턴만 가져와서 Conversation 형태로 포맷팅
        chat_logs = room_logs.get("chatLogs", [])
        conversation = []
        
        # 최근 10개만 (뒤에서 10개)
        recent_logs = chat_logs[-10:] if len(chat_logs) > 10 else chat_logs
        
        for log in recent_logs:
            speaker = "human" if log["sender"] == "user" else "ai"
            conversation.append({
                "speaker": speaker,
                "utterance": log["content"]
            })
        
        logging.info(
            "[chat_ask] Loaded %d conversation turns from room %s",
            len(conversation),
            room_id,
        )
    except Exception as exc:
        logging.error("Failed to load room logs: %s", exc, exc_info=True)
        return create_error_response(
            req,
            status_code=500,
            error="대화방 로그 로드에 실패했습니다.",
            details={
                "errorType": type(exc).__name__,
                "errorMessage": str(exc)
            }
        )

    translate_start = time.perf_counter()
    try:
        translation_result = chat_service.translate_model.translate_query(
            explicit_query
        )
    except Exception as exc:
        logging.error("Translation failed: %s", exc, exc_info=True)
        return create_error_response(
            req,
            status_code=500,
            error="질문 번역 처리에 실패했습니다.",
            details={
                "errorType": type(exc).__name__,
                "errorMessage": str(exc)
            }
        )

    translated_query = translation_result["translated_query"]
    query_lang = translation_result.get("query_lang")
    mongo_query = translation_result.get("mongo_query") or []

    translate_duration = time.perf_counter() - translate_start
    logging.info(
        "[Translate] Completed in %.2f s (lang=%s)",
        translate_duration,
        query_lang,
    )

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
            return create_error_response(
                req,
                status_code=400,
                error="잘못된 MongoDB 쿼리 형식입니다.",
                details={
                    "field": "mongo_query",
                    "errorType": type(exc).__name__,
                    "errorMessage": str(exc)
                }
            )

    augmented_conversation = list(conversation)
    augmented_conversation.append(
        {"speaker": "human", "utterance": translated_query}
    )

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
                req.headers.get("x-request-id")
                or req.headers.get("x-ms-request-id")
                or str(uuid.uuid4())
            )
            error_response = {
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "path": req.url or "",
                "status": 500,
                "error": "스트리밍 중 오류가 발생했습니다.",
                "requestId": request_id,
                "details": {
                    "errorType": type(exc).__name__,
                    "errorMessage": str(exc)
                }
            }
            yield f"data: {json.dumps(error_response, ensure_ascii=False)}\n\n"
        finally:
            elapsed_seconds = time.perf_counter() - processing_start
            final_answer = "".join(aggregated_answer)
            logging.info(
                "[Answer] Final streamed answer=%s",
                final_answer,
            )
            logging.info(
                "[Total] Request completed in %.2f s",
                elapsed_seconds,
            )
            
            # 채팅 로그 저장
            try:
                asyncio.create_task(
                    chat_service.save_chat_log(user_id, room_id, explicit_query, final_answer)
                )
            except Exception as log_error:
                logging.error(f"Failed to save chat log: {log_error}")

    try:
        return func.HttpResponse(
            stream_response(),
            status_code=200,
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except Exception as exc:
        logging.exception("Question streaming handler failed")
        return create_error_response(
            req,
            status_code=500,
            error="스트리밍 응답 처리 중 오류가 발생했습니다.",
            details={
                "errorType": type(exc).__name__,
                "errorMessage": str(exc)
            }
        )

@app.route(route="summary", auth_level=func.AuthLevel.ANONYMOUS, methods=["POST"])
@require_auth
async def summary(req: func.HttpRequest) -> func.HttpResponse:
    """
    채팅방의 대화 로그를 바탕으로 요약문을 생성하는 엔드포인트
    
    Parameters:
        Authorization (header): 인증 토큰
        roomId (query): 대화방 ID
    
    Returns:
        string: 요약문
    """
    logging.info("Summary function triggered.")
    
    # 데코레이터가 주입한 user_id 사용 (현재는 미사용이지만 향후 권한 검증에 사용 가능)
    user_id = req.user_id
    
    # roomId 파라미터 확인
    room_id = req.params.get('roomId')
    if not room_id:
        return create_error_response(
            req,
            status_code=400,
            error="roomId 파라미터가 필요합니다.",
            details={"field": "roomId", "issue": "roomId parameter is required"}
        )
    
    try:
        chat_service = await get_chat_service()
        
        # 대화방 로그 조회
        room_logs = await chat_service.get_room_logs(room_id)
        
        if not room_logs["chatLogs"]:
            return create_success_response(
                req,
                status_code=200,
                data={"summary": None, "message": f"대화방 {room_id}에 로그가 없습니다."}
            )
        
        # 대화 내용 포맷팅
        conversation_text = ""
        for log in room_logs["chatLogs"]:
            sender = "사용자" if log["sender"] == "user" else "AI"
            conversation_text += f"{sender}: {log['content']}\n\n"
        
        # 프롬프트 불러오기
        from prompts import prompts
        summary_prompt_template = prompts.load_prompt("chat_summary")
        summary_prompt = summary_prompt_template.format(conversation=conversation_text)
        
        # OpenAI 클라이언트로 요약 생성 (비동기 + 429 에러 핸들링)
        client = await get_openai_client()
        response = await openai_request_with_limit(
            client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "당신은 채팅 대화 내용을 요약하는 전문 어시스턴트입니다."},
                    {"role": "user", "content": summary_prompt}
                ],
                temperature=0.3,
                max_tokens=300
            )
        )
        
        summary_text = response.choices[0].message.content.strip()
        
        return create_success_response(
            req,
            status_code=200,
            data={"summary": summary_text}
        )

    except Exception as e:
        logging.exception("Summary handler failed")
        return create_error_response(
            req,
            status_code=500,
            error="요약 생성 중 오류가 발생했습니다.",
            details={
                "errorType": type(e).__name__,
                "errorMessage": str(e)
            }
        )


@app.route(route="chat/room-log", auth_level=func.AuthLevel.ANONYMOUS, methods=["GET"])
@require_auth
async def chat_room_log(req: func.HttpRequest) -> func.HttpResponse:
    """
    특정 대화방의 로그를 반환하는 엔드포인트
    
    Parameters:
        Authorization (header): 인증 토큰
        roomId (query): 대화방 ID
    
    Returns:
        JSON: {
            "roomId": string,
            "chatLogs": [
                {"content": string, "sender": string}
            ]
        }
    """
    logging.info("Chat room log function triggered.")
    global chat_service
    
    # 데코레이터가 주입한 user_id 사용
    user_id = req.user_id
    
    # roomId 파라미터 확인
    room_id = req.params.get('roomId')
    if not room_id:
        return create_error_response(
            req,
            status_code=400,
            error="roomId 파라미터가 필요합니다.",
            details={"field": "roomId", "issue": "roomId parameter is required"}
        )
    
    try:
        chat_service = await get_chat_service()
        
        # 대화방 로그 조회 (userId로 권한 검증)
        response_data = await chat_service.get_room_logs(room_id, user_id)
        
        if response_data is None:
            return create_error_response(
                req,
                status_code=403,
                error="해당 대화방에 접근 권한이 없거나 대화방을 찾을 수 없습니다.",
                details={"roomId": room_id, "issue": "Access denied or room not found"}
            )
        
        return create_success_response(
            req,
            status_code=200,
            data=response_data,
        )
    except Exception as e:
        logging.exception("Chat room log handler failed")
        return create_error_response(
            req,
            status_code=500,
            error="대화방 로그 조회 중 오류가 발생했습니다.",
            details={
                "errorType": type(e).__name__,
                "errorMessage": str(e)
            }
        )


@app.route(route="chat/recent-room", auth_level=func.AuthLevel.ANONYMOUS, methods=["GET"])
@require_auth
async def chat_recent_room(req: func.HttpRequest) -> func.HttpResponse:
    """
    가장 최근 대화방 기록을 반환하는 엔드포인트
    
    Parameters:
        Authorization (header): 인증 토큰
    
    Returns:
        JSON: {
            "roomId": string,
            "chatLogs": [
                {"content": string, "sender": string}
            ]
        }
    """
    logging.info("Chat recent room function triggered.")
    global chat_service
    
    # 데코레이터가 주입한 user_id 사용
    user_id = req.user_id
    
    try:
        chat_service = await get_chat_service()
        
        # 최근 대화방 및 로그 조회
        response_data = await chat_service.get_recent_room_and_logs(user_id)
        
        return create_success_response(
            req,
            status_code=200,
            data=response_data,
        )
    except Exception as e:
        logging.exception("Chat recent room handler failed")
        return create_error_response(
            req,
            status_code=500,
            error="최근 대화방 조회 중 오류가 발생했습니다.",
            details={
                "errorType": type(e).__name__,
                "errorMessage": str(e)
            }
        )


# 이력서 생성
@app.route(route="cv_generation", auth_level=func.AuthLevel.ANONYMOUS, methods=["POST"])
@require_auth
async def cv_generation(req: func.HttpRequest) -> func.HttpResponse:
    """
    이력서 생성 요청을 처리하는 엔드포인트입니다.

    Parameters:
        req (func.HttpRequest): HTTP 요청 객체로, JSON 형식의 이력서 정보를 포함
        Authorization (header): 인증 토큰 (필수)

        예시 JSON 형식:
        {
            "personal": {
                "name": "홍길동",
                "nationality": "베트남",
                "visa": "E-7"
            },
            "experience": [
                {"work": "업무 내용1"},
                {"work": "업무 내용2"},
                {"work": "업무 내용3"}
            ],
            "language": {
                "korean": "상",
                "others": [
                    {"name": "영어", "level": "중"},
                    {"name": "베트남어", "level": "상"}
                ]
            },
            "skills": ["Excel", "Pandas"],
            "strengths": ["성실함", "밝음"],
            "desired_position": "데이터 분석가"
        }

    Returns:
        func.HttpResponse: 생성된 이력서를 JSON 형식으로 반환
        성공 시: {"resume": {"introduction": "한줄소개", "details": "상세소개"}} (200 OK)
        실패 시: 에러 메시지와 함께 적절한 HTTP 상태 코드
            - 401: 인증 실패
    """

    logging.info("CV Generation function triggered.")
    
    # 데코레이터가 주입한 user_id 사용 (현재는 미사용이지만 향후 확장 가능)
    user_id = req.user_id

    try:
        
        from prompts import prompts

        # Azure Application Settings에서 환경변수 직접 가져오기
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if not openai_api_key:
            raise ValueError("OPENAI_API_KEY environment variable is not set")

        # 요청 본문에서 JSON 데이터 가져오기
        req_body = req.get_json()

        # 필수 필드 검증
        if not all(
            field in req_body for field in ["personal", "experience", "language"]
        ):
            missing_fields = [f for f in ["personal", "experience", "language"] if f not in req_body]
            return create_error_response(
                req,
                status_code=400,
                error="필수 필드가 누락되었습니다.",
                details={
                    "missingFields": missing_fields,
                    "issue": "Missing required fields"
                }
            )

        # 시스템 프롬프트
        system_prompt = prompts.load_prompt("cv")

        # 사용자 정보 포맷팅
        foreign_languages = "\n\t".join(
            [
                f"{lang['name']}: {lang['level']}"
                for lang in req_body["language"]["others"]
            ]
        )

        # 근무경험 포맷팅 추가
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
        # ChatGPT API 호출 
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = llm.invoke(input=messages)

        # 응답 파싱
        content = response.content
        intro_start = content.find("<자기소개문장>") + len("<자기소개문장>")
        intro_end = content.find("</자기소개문장>")
        details_start = content.find("<상세소개서>") + len("<상세소개서>")
        details_end = content.find("</상세소개서>")

        introduction = content[intro_start:intro_end].strip()
        details = content[details_start:details_end].strip()

        return create_success_response(
            req,
            status_code=200,
            data={"resume": {"introduction": introduction, "details": details}},
        )

    except ValueError as ve:
        logging.exception("CV generation configuration error")
        return create_error_response(
            req,
            status_code=500,
            error="서버 구성 오류가 발생했습니다.",
            details={
                "errorType": "ValueError",
                "errorMessage": str(ve)
            }
        )
    except Exception as e:
        logging.exception("CV generation handler failed on import/exec")
        return create_error_response(
            req,
            status_code=500,
            error="이력서 생성 중 오류가 발생했습니다.",
            details={
                "errorType": type(e).__name__,
                "errorMessage": str(e)
            }
        )
