import azure.functions as func
import logging
import os
import json
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from pymongo import MongoClient
from query_model import ChatModel
from prompts import prompts


app = func.FunctionApp()

load_dotenv(verbose=True)


class ChatService:
    """
    핵심 AI 채팅 서비스를 구현한 클래스입니다.
    """

    def __init__(self):
        self.initialize()

    def initialize(self):
        """
        이 함수는 모델, DB 설정을 초기화하고, 환경 변수를 설정하는 함수입니다.
        Returns:
            dict: 설정 정보가 담긴 딕셔너리
        """
        logging.info("====== Application initialization started ======")

        with open('configs/config.json', "r", encoding='utf-8') as f:
            self.config = json.load(f)
            self.db_name = self.config['path']['db_name']
            self.collection_name = self.config['path']['collection_name']
        
        self.model = ChatModel(self.config)

        # MongoDB 클러스터 URI 환경변수 설정
        os.environ["MONGODB_URI"] = os.getenv("MONGODB_URI")
        os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY")

        logging.info("model and environment variables initialized")

        try:
            self.client = MongoClient(os.environ["MONGODB_URI"], ssl=True)
            logging.info(f"MongoDB INFO : {self.client.server_info()}")

            self.collection = self.client[self.db_name][self.collection_name]
            logging.info("database initialized successfully")
        except Exception as e:
            logging.error(f"Error loading database: {str(e)}")
            raise
    # Translate & MongoQuery -> "translated", "mongo_query" 로 묶인 result가 옴

    # 키워드 기반 검색 함수
    def get_query_model_response_with_docs(self,query_text, mongo_query):
        """
        키워드 기반 하이브리드 검색 모델로부터 답변을 생성하고 검색된 문서들의 인덱스를 반환
        """
        try:
            # 빈 대화 히스토리로 시작
            conversation_history = []
            
            # 키워드 기반 모델 답변 생성
            response = self.model.generate_ai_response(
                conversation_history, 
                query_text, 
                self.collection, 
                mongo_query=mongo_query
            )
            
            return {
                "answer": response["answer"],
                "retrieved_doc_ids": response["retrieved_doc_ids"],
                "retrieved_docs": response["retrieved_docs"]
            }
            
        except Exception as e:
            print(f"오류 발생: {e}")
            return {
                "answer": "ERROR",
                "retrieved_doc_ids": [],
                "retrieved_docs": []
            }

# 전역 변수 선언
chat_service = None

# 사용자 요청 수신
@app.route(route="question", auth_level=func.AuthLevel.ANONYMOUS)
def question(req: func.HttpRequest) -> func.HttpResponse:
    """
    이 함수는 HTTP POST 요청을 통해 사용자의 대화 내용을 받고,
    AI 응답을 생성하는 엔드포인트입니다.

    Parameters:
        req (func.HttpRequest): HTTP 요청 객체로, JSON 형식의 대화 내용을 포함한다.

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
            - 400: 잘못된 요청 (대화 내용 누락 등)
            - 500: 서버 내부 오류

    Notes:
        - 대화 내용에서 마지막 사용자(human) 발화만 추출하여 처리
        - 모든 응답은 한글을 포함한 유니코드 문자를 그대로 유지 (ensure_ascii=False)
    """

    logging.info("Question function triggered.")

    global chat_service

    if not chat_service:
        chat_service = ChatService()

    try:
        # 요청 본문에서 JSON 데이터를 가져오고, Conversation 필드를 추출
        req_body = req.get_json()
        conversation = req_body.get("Conversation", [])

        # Conversation이 없으면 오류 메시지를 반환
        if not conversation:
            return func.HttpResponse("No conversation data provided", status_code=400)

        # 마지막으로 입력된 사용자 발화를 추출
        user_query = next(
            (
                item["utterance"]
                for item in reversed(conversation)
                if item["speaker"] == "human"
            ),
            None,
        )

        # 사용자 쿼리 검증
        if user_query is None:
            return func.HttpResponse("No user utterance found", status_code=400)

        # 응답 생성
        response = chat_service.get_query_model_response_with_docs(conversation, user_query)

        # 응답에서 references 제외하고 answer만 반환
        # 클라이언트에게 답변 텍스트만 전달 된다.
        return func.HttpResponse(
            json.dumps({"answer": response["answer"]}, ensure_ascii=False),
            mimetype="application/json",
        )

    except Exception as e:
        logging.error(f"Error processing question: {str(e)}")
        return func.HttpResponse(f"An error occurred: {str(e)}", status_code=500)


# 이력서 생성
@app.route(route="cv_generation", auth_level=func.AuthLevel.ANONYMOUS)
def cv_generation(req: func.HttpRequest) -> func.HttpResponse:
    """
    이력서 생성 요청을 처리하는 엔드포인트입니다.

    Parameters:
        req (func.HttpRequest): HTTP 요청 객체로, JSON 형식의 이력서 정보를 포함

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
    """

    logging.info("CV Generation function triggered.")

    # OpenAI API 키 환경변수 설정
    os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY")

    try:
        # 요청 본문에서 JSON 데이터 가져오기
        req_body = req.get_json()

        # 필수 필드 검증
        if not all(
            field in req_body for field in ["personal", "experience", "language"]
        ):
            return func.HttpResponse("Missing required fields", status_code=400)

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

        return func.HttpResponse(
            json.dumps(
                {"resume": {"introduction": introduction, "details": details}},
                ensure_ascii=False,
            ),
            mimetype="application/json",
        )

    except Exception as e:
        logging.error(f"Error generating CV: {str(e)}")
        return func.HttpResponse(f"An error occurred: {str(e)}", status_code=500)


# 테스트 엔드포인트
@app.route(route="get_test/{param}", auth_level=func.AuthLevel.ANONYMOUS)
def get_echo_call(req: func.HttpRequest) -> func.HttpResponse:
    """
    테스트용 엔드포인트입니다.

    Parameters:
        req (func.HttpRequest): HTTP 요청 객체
        param: URL 경로에서 추출할 파라미터

    Returns:
        func.HttpResponse: 입력받은 파라미터를 그대로 반환
    """

    param = req.route_params.get("param")
    return func.HttpResponse(json.dumps({"param": param}), mimetype="application/json")
