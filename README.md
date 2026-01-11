# HelloWorld-AI-Azure
Azure CI/CD 배포용
- 패키지는 requirements.txt로 관리
- 배포 관련 사항은 Actions 에서 로그 확인할 것
- **반드시** 로컬 테스트를 거친 후 develop 브랜치 push 진행할 것
    - 만약 추가 기능 개발이 필요한 사안의 경우는 FEAT-01과 같은 형태로 브랜치 딴 후에 develop에 병합
    - 브랜치에 대한 설명은 노션에서 공유
- api 테스트는 postman workspace에서 진행

## 목차
- [로컬 테스트 가이드](#로컬-테스트-가이드)
- [Azure Functions Postman 테스트 가이드](#azure-functions-postman-테스트-가이드)
- [1. 테스트 엔드포인트 (GET /api/get_test)](#1-테스트-엔드포인트-get-apiget_test)
- [2. 질문 엔드포인트 (POST /api/question)](#2-질문-엔드포인트-post-apiquestion)
- [3. 이력서 생성 엔드포인트 (POST /api/cv_generation)](#3-이력서-생성-엔드포인트-post-apicv_generation)
- [빠른 테스트 체크리스트](#빠른-테스트-체크리스트)
- [문제 해결 가이드](#문제-해결-가이드)

---

## 로컬 테스트 가이드
- 여기서 작동 안하면 코드 문제 // 확인해보고 커밋할 것!

```bash
# git clone
git clone https://github.com/HelloWorld-AICC/HelloWorld-AI-Azure.git
# Azure functions Core tool 설치 (윈도우 기준)
npm install -g azure-functions-core-tools@4 --unsafe-perm true

# git clone 했던 HelloWorld-AI-Azure 로 이동 후 func start
func start

# 아래 메시지 뜰 때까지 대기
[2025-11-09T14:57:31.481Z] Worker process started and initialized.

Functions:

        cv_generation:  http://localhost:7071/api/cv_generation

        get_echo_call: [GET] http://localhost:7071/api/get_test?{param}

        question:  http://localhost:7071/api/question

# Postman에서 테스트 해보면 됨
```

---

## Azure Functions Postman 테스트 가이드

베이스 URL: `http://helloworld-func-app-v2-e2gad2h8gwdbatha.koreacentral-01.azurewebsites.net`

---

## 1. 테스트 엔드포인트 (GET /api/get_test)

### 요청 설정
- **Method**: `GET`
- **URL**: `http://helloworld-func-app-v2-e2gad2h8gwdbatha.koreacentral-01.azurewebsites.net/api/get_test?param=hello`
- **Headers**: 없음
- **Body**: 없음

### Postman 설정 단계
1. Method를 `GET`으로 선택
2. URL에 다음 입력:
   ```
   http://helloworld-func-app-v2-e2gad2h8gwdbatha.koreacentral-01.azurewebsites.net/api/get_test?param=hello
   ```
3. Send 클릭

### 예상 응답 (200 OK)
```json
{
    "param": "hello"
}
```

### 테스트 변형
- `?param=test` → `{"param": "test"}`
- `?param=안녕하세요` → `{"param": "안녕하세요"}`
- 파라미터 없이 요청 시 → `400 Bad Request`

---

## 2. 질문 엔드포인트 (POST /api/question)

### 요청 설정
- **Method**: `POST`
- **URL**: `http://helloworld-func-app-v2-e2gad2h8gwdbatha.koreacentral-01.azurewebsites.net/api/question`
- **Headers**:
  ```
  Content-Type: application/json
  ```
- **Body** (raw JSON):

### Postman 설정 단계
1. Method를 `POST`로 선택
2. URL에 다음 입력:
   ```
   http://helloworld-func-app-v2-e2gad2h8gwdbatha.koreacentral-01.azurewebsites.net/api/question
   ```
3. Headers 탭:
   - Key: `Content-Type`
   - Value: `application/json`
4. Body 탭:
   - `raw` 선택
   - 드롭다운에서 `JSON` 선택
   - 아래 스키마에 맞게 입력

### 입력 본문 스키마
```json
{
    "Conversation": [
        { "speaker": "human", "utterance": "..." },
        { "speaker": "ai", "utterance": "..." }
    ],
    "query": "<현재 사용자 질문 (원문 언어)>",
    "mongo_query": [
        { "$search": { "index": "text", "compound": { "should": [] } } }
    ]
}
```
- `Conversation` *(선택)*: 직전 대화 기록. 새 입력은 서버가 자동으로 번역하여 이 배열의 마지막에 append 됩니다.
- `query` *(필수)*: 사용자가 입력한 **원문 메시지**. 한국어가 아니어도 되며, 서버가 GPT-4o-mini 기반 `TranslateModel`로 번역/언어 감지를 수행합니다.
- `mongo_query` *(선택)*: MongoDB 파이프라인을 직접 지정하고 싶을 때 제공. 값이 없으면 LLM이 `QUERY_TRANSLATE_PROMPT`를 활용해 자동 생성합니다.

> ⚙️ 내부 동작 요약
> 1. 입력 `query`를 GPT-4o-mini가 번역하고 `query_lang`을 판별합니다.
> 2. 생성된 번역문과 `mongo_query`가 `ChatModel`로 전달됩니다.
> 3. `ChatModel`은 탐색된 문서를 기반으로 **사용자 언어(`query_lang`)**에 맞춰 답변을 생성합니다.

### 출력 스키마
```json
{
    "answer": "<탐색 결과를 요약한 응답 (입력 언어와 동일)>"
}
```
- `answer`: 검색 결과를 반영한 최종 텍스트. 시스템이 감지한 언어(`query_lang`)로 응답합니다.

### 테스트 케이스 1: 한국어 질문
```json
{
    "Conversation": [
        { "speaker": "human", "utterance": "E-7 비자가 뭔가요?" },
        { "speaker": "ai", "utterance": "E-7 비자는 전문 인력을 위한 취업 비자입니다." }
    ],
    "query": "신청 방법을 알려주세요"
}
```

### 테스트 케이스 2: 다국어 질문 (태국어)
```json
{
    "Conversation": [
        { "speaker": "human", "utterance": "E-7 visa 신청 방법" }
    ],
    "query": "ภรรยาชาวไทยของผมต้องทำอย่างไรเพื่อขอวีซ่าทำงานในเกาหลี?"
}
```

### 예상 응답 (200 OK)
```json
{
    "answer": "ภรรยาควรตรวจสอบว่าตรงตามเงื่อนไข E-7 หรือไม่..." 
}
```
→ 입력 언어가 태국어였으므로, 응답도 태국어로 반환됩니다.

### 오류 케이스
- Conversation 누락:
  ```json
  {}
  ```
  → `400 Bad Request: "No conversation data provided"`

- human 발화 없음:
  ```json
  {
      "Conversation": [
          {
              "speaker": "ai",
              "utterance": "안녕하세요"
          }
      ]
  }
  ```
  → `400 Bad Request: "No user utterance found"`

---

## 3. 이력서 생성 엔드포인트 (POST /api/cv_generation)

### 요청 설정
- **Method**: `POST`
- **URL**: `http://helloworld-func-app-v2-e2gad2h8gwdbatha.koreacentral-01.azurewebsites.net/api/cv_generation`
- **Headers**:
  ```
  Content-Type: application/json
  ```
- **Body** (raw JSON):

### Postman 설정 단계
1. Method를 `POST`로 선택
2. URL에 다음 입력:
   ```
   http://helloworld-func-app-v2-e2gad2h8gwdbatha.koreacentral-01.azurewebsites.net/api/cv_generation
   ```
3. Headers 탭:
   - Key: `Content-Type`
   - Value: `application/json`
4. Body 탭:
   - `raw` 선택
   - 드롭다운에서 `JSON` 선택
   - 아래 JSON 입력:

### 테스트 케이스: 완전한 이력서 데이터
```json
{
    "personal": {
        "name": "응우옌 반 A",
        "nationality": "베트남",
        "visa": "E-7"
    },
    "experience": [
        {
            "work": "베트남 하노이 소재 IT 회사에서 3년간 웹 개발 업무 수행"
        },
        {
            "work": "Python, Django 프레임워크를 활용한 백엔드 개발"
        },
        {
            "work": "데이터베이스 설계 및 최적화 경험"
        }
    ],
    "language": {
        "korean": "중",
        "others": [
            {
                "name": "영어",
                "level": "상"
            },
            {
                "name": "베트남어",
                "level": "상"
            }
        ]
    },
    "skills": [
        "Python",
        "Django",
        "PostgreSQL",
        "Git",
        "Docker"
    ],
    "strengths": [
        "빠른 학습 능력",
        "팀워크",
        "문제 해결 능력"
    ],
    "desired_position": "백엔드 개발자"
}
```

### 테스트 케이스: 최소 필수 데이터
```json
{
    "personal": {
        "name": "홍길동",
        "nationality": "필리핀",
        "visa": "E-9"
    },
    "experience": [
        {
            "work": "제조업 현장 근무 2년"
        }
    ],
    "language": {
        "korean": "하",
        "others": [
            {
                "name": "영어",
                "level": "중"
            }
        ]
    },
    "skills": [
        "기계 조작"
    ],
    "strengths": [
        "성실함"
    ]
}
```

### 예상 응답 (200 OK)
```json
{
    "resume": {
        "introduction": "3년 경력의 베트남 출신 백엔드 개발자로, Python과 Django를 활용한 웹 개발 전문가입니다.",
        "details": "안녕하세요, 응우옌 반 A입니다.\n\n저는 베트남 하노이에서 3년간 IT 업계에서 근무하며...(중략)...귀사의 발전에 기여하고 싶습니다."
    }
}
```

### 오류 케이스
- 필수 필드 누락:
  ```json
  {
      "personal": {
          "name": "홍길동"
      }
  }
  ```
  → `400 Bad Request: "Missing required fields"`

---

## 빠른 테스트 체크리스트

### ✅ 1단계: 기본 연결 확인
```
GET /api/get_test?param=hello
→ 200 OK 응답 확인
```

### ✅ 2단계: 질문 기능 확인
```
POST /api/question
Body: {"Conversation": [{"speaker": "human", "utterance": "안녕하세요"}]}
→ 200 OK + answer 필드 확인
```

### ✅ 3단계: 이력서 생성 확인
```
POST /api/cv_generation
Body: 위의 완전한 이력서 데이터
→ 200 OK + resume.introduction, resume.details 필드 확인
```

---

## 문제 해결 가이드

### 404 Not Found
- URL 경로 확인 (`/api/` 접두사 필수)
- Method 확인 (GET/POST)
- 배포 상태 확인

### 400 Bad Request
- Content-Type 헤더 확인
- JSON 형식 유효성 검사
- 필수 필드 포함 여부 확인

### 500 Internal Server Error
- Azure Portal에서 로그 확인
- 환경 변수 설정 확인 (MONGODB_URI, OPENAI_API_KEY)
- 패키지 설치 확인 (requirements.txt)
