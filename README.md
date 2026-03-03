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
- [API 엔드포인트 명세](#api-엔드포인트-명세)
  - [1. 테스트 엔드포인트 (GET /api/get_echo_call)](#1-테스트-엔드포인트-get-apiget_echo_call)
  - [2. 채팅 스트리밍 엔드포인트 (POST /api/chat/ask)](#2-채팅-스트리밍-엔드포인트-post-apichatask)
  - [3. 대화 요약 엔드포인트 (POST /api/summary)](#3-대화-요약-엔드포인트-post-apisummary)
  - [4. 대화방 로그 조회 (GET /api/chat/room-log)](#4-대화방-로그-조회-get-apichatroom-log)
  - [5. 최근 대화방 조회 (GET /api/chat/recent-room)](#5-최근-대화방-조회-get-apichatrecent-room)
  - [6. 채팅방 목록 조회 (GET /api/chat/rooms)](#6-채팅방-목록-조회-get-apichatrooms)
  - [7. 채팅방 삭제 (DELETE /api/chat/room)](#7-채팅방-삭제-delete-apichatroom)
  - [8. 이력서 생성 엔드포인트 (POST /api/cv_generation)](#8-이력서-생성-엔드포인트-post-apicv_generation)
  - [9. 채팅방 요약 조회 (GET /api/chat/room-summary)](#9-채팅방-요약-조회-get-apichatroom-summary)
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

## API 엔드포인트 명세

베이스 URL: `http://helloworld-func-app-v2-e2gad2h8gwdbatha.koreacentral-01.azurewebsites.net`

### 전체 엔드포인트 요약

| Method | Path | 인증 | 설명 |
|--------|------|------|------|
| GET | `/api/get_echo_call?param=<val>` | 불필요 | 헬스 체크 |
| POST | `/api/question` | 필요 | 비스트리밍 RAG Q&A (레거시) |
| POST | `/api/chat/ask?roomId=<id>` | 필요 | SSE 스트리밍 채팅 |
| POST | `/api/summary?roomId=<id>` | 필요 | 채팅방 요약 생성 및 저장 |
| GET | `/api/chat/room-log?roomId=<id>` | 필요 | 채팅방 전체 로그 조회 |
| GET | `/api/chat/recent-room` | 필요 | 최근 채팅방 + 로그 조회 |
| POST | `/api/chat/create-room` | 필요 | 채팅방 생성 |
| GET | `/api/chat/user-rooms` | 필요 | 사용자 채팅방 목록 조회 |
| GET | `/api/chat/user-summaries` | 필요 | 마이페이지용 상담 요약 목록 |
| GET | `/api/chat/room-summary?roomId=<id>` | 필요 | 특정 채팅방 요약 조회 |
| DELETE | `/api/chat/user-rooms` | 필요 | 전체 채팅방 및 로그 삭제 |
| DELETE | `/api/chat/room?roomId=<id>` | 필요 | 단일 채팅방 및 로그 삭제 |
| POST | `/api/cv_generation` | 필요 | 한국어 이력서 생성 |

### rooms 컬렉션 필드 구조

채팅방 생성 이후 대화가 진행되면서 아래 필드들이 자동으로 채워집니다.

| 필드 | 타입 | 설명 |
|------|------|------|
| `roomId` | string | MongoDB ObjectId 문자열 |
| `roomTitle` | string \| null | 첫 대화 저장 시 자동 생성되는 30자 이내 채팅방 제목 |
| `roomSummary` | string \| null | `/api/summary` 호출 시 생성·저장되는 상담 요약문 |
| `createdAt` | ISO8601 | 채팅방 생성 시각 |
| `updatedAt` | ISO8601 | 마지막 대화 저장 시각 |

---

## 1. 테스트 엔드포인트 (GET /api/get_echo_call)

### 역할
서버 연결 및 기본 동작을 확인하기 위한 테스트 엔드포인트입니다. 입력받은 파라미터를 그대로 반환합니다.

### 요청 설정
- **Method**: `GET`
- **URL**: `/api/get_echo_call?param=hello`
- **인증**: 불필요
- **Query Parameters**:
  - `param` (필수): 테스트할 문자열

### 예시 요청
```
GET /api/get_echo_call?param=hello
```

### 예상 응답 (200 OK)
```json
{
    "timestamp": "2026-01-19T12:34:56.789Z",
    "path": "/api/get_echo_call?param=hello",
    "status": 200,
    "error": "OK",
    "requestId": "abc-123-def-456",
    "data": {
        "param": "hello"
    }
}
```

---

## 2. 채팅 스트리밍 엔드포인트 (POST /api/chat/ask)

### 역할
실시간 SSE(Server-Sent Events) 스트리밍으로 AI 응답을 제공합니다. MongoDB에서 대화방 히스토리를 자동으로 로드하고, 현재 질문과 결합하여 답변을 생성합니다. 응답 완료 후 대화 로그가 자동 저장되며, 첫 번째 대화 저장 시 `roomTitle`이 자동 생성됩니다.

### 요청 설정
- **Method**: `POST`
- **URL**: `/api/chat/ask?roomId=<ROOM_ID>`
- **인증**: 필수 (`Authorization: Bearer <JWT_TOKEN>`)
- **Headers**:
  - `Content-Type: application/json`
  - `Authorization: Bearer <JWT_TOKEN>`
- **Query Parameters**:
  - `roomId` (필수): 대화방 ID (24자리 MongoDB ObjectId)

### Request Body
```json
{
    "query": "E-7 비자 신청 서류가 뭔가요?"
}
```

**필드 설명**:
- `query` (필수): 사용자 질문 문자열

### 예상 응답 (200 OK, text/event-stream)
```
data: {"type":"content","content":"E"}

data: {"type":"content","content":"-7"}

data: {"type":"content","content":" 비자"}
...
```

### 동작 흐름
1. `roomId`로 MongoDB에서 최근 10개 대화 로그 조회
2. 조회한 히스토리 + 현재 `query`를 결합하여 RAG 검색 + GPT 스트리밍 응답
3. 완료 후 자동으로 대화 로그 저장
4. `roomTitle`이 없는 방이면 질문·답변을 바탕으로 30자 이내 제목 자동 생성 후 저장

---

## 3. 대화 요약 엔드포인트 (POST /api/summary)

### 역할
특정 대화방의 전체 대화 내용을 요약합니다. 생성된 요약문은 응답으로 반환됨과 동시에 `rooms` 컬렉션의 `roomSummary` 필드에 저장됩니다. 이후 `/api/chat/user-summaries` 조회 시 별도 생성 없이 바로 반환됩니다.

### 요청 설정
- **Method**: `POST`
- **URL**: `/api/summary?roomId=<ROOM_ID>`
- **인증**: 필수 (`Authorization: Bearer <JWT_TOKEN>`)
- **Headers**:
  - `Authorization: Bearer <JWT_TOKEN>`
- **Query Parameters**:
  - `roomId` (필수): 대화방 ID

### Request Body
없음 (body 전송 불필요)

### 예상 응답 (200 OK)
```json
{
    "timestamp": "2026-01-19T12:34:56.789Z",
    "path": "/api/summary?roomId=abc123",
    "status": 200,
    "error": "OK",
    "requestId": "abc-123-def-456",
    "data": {
        "summary": "E-7 비자 신청 절차와 필요 서류에 대한 상담"
    }
}
```

---

## 4. 대화방 로그 조회 (GET /api/chat/room-log)

### 역할
특정 대화방의 전체 대화 로그를 시간순으로 반환합니다. 권한 검증이 포함되어 있어 해당 사용자의 대화방만 조회 가능합니다. `roomTitle`도 함께 반환됩니다.

### 요청 설정
- **Method**: `GET`
- **URL**: `/api/chat/room-log?roomId=<ROOM_ID>`
- **인증**: 필수 (`Authorization: Bearer <JWT_TOKEN>`)
- **Headers**:
  - `Authorization: Bearer <JWT_TOKEN>`
- **Query Parameters**:
  - `roomId` (필수): 대화방 ID

### 예상 응답 (200 OK)
```json
{
    "timestamp": "2026-01-19T12:34:56.789Z",
    "path": "/api/chat/room-log?roomId=abc123",
    "status": 200,
    "error": "OK",
    "requestId": "abc-123-def-456",
    "data": {
        "roomId": "abc123",
        "roomTitle": "E-7 비자 신청 절차 문의",
        "chatLogs": [
            {"content": "E-7 비자 신청 서류가 뭔가요?", "sender": "user"},
            {"content": "E-7 비자 신청에는 다음 서류가 필요합니다...", "sender": "bot"}
        ]
    }
}
```

---

## 5. 최근 대화방 조회 (GET /api/chat/recent-room)

### 역할
사용자의 가장 최근에 업데이트된 대화방과 해당 대화 로그를 반환합니다. `roomTitle`도 함께 반환됩니다.

### 요청 설정
- **Method**: `GET`
- **URL**: `/api/chat/recent-room`
- **인증**: 필수 (`Authorization: Bearer <JWT_TOKEN>`)
- **Headers**:
  - `Authorization: Bearer <JWT_TOKEN>`

### 예상 응답 (200 OK)
```json
{
    "timestamp": "2026-01-19T12:34:56.789Z",
    "path": "/api/chat/recent-room",
    "status": 200,
    "error": "OK",
    "requestId": "abc-123-def-456",
    "data": {
        "roomId": "abc123",
        "roomTitle": "E-7 비자 신청 절차 문의",
        "chatLogs": [
            {"content": "E-7 비자가 뭔가요?", "sender": "user"},
            {"content": "E-7 비자는...", "sender": "bot"}
        ]
    }
}
```

---

## 6. 채팅방 목록 조회 (GET /api/chat/rooms)

### 역할
JWT 토큰을 가진 사용자가 소유한 모든 채팅방의 제목 및 roomId 목록을 반환합니다. 최신 업데이트 순으로 정렬됩니다.

### 요청 설정
- **Method**: `GET`
- **URL**: `/api/chat/rooms`
- **인증**: 필수 (`Authorization: Bearer <JWT_TOKEN>`)
- **Headers**:
  - `Authorization: Bearer <JWT_TOKEN>`

### 예상 응답 (200 OK)
```json
{
    "timestamp": "2026-01-23T12:34:56.789Z",
    "path": "/api/chat/rooms",
    "status": 200,
    "error": "OK",
    "requestId": "abc-123-def-456",
    "data": {
        "userId": 1,
        "totalRooms": 3,
        "rooms": [
            {
                "roomId": "679234abc123def456789012",
                "title": "취업 상담",
                "createdAt": "2026-01-20T10:30:00",
                "updatedAt": "2026-01-23T15:20:00"
            },
            {
                "roomId": "678abc123def456789012345",
                "title": "이력서 작성",
                "createdAt": "2026-01-18T09:00:00",
                "updatedAt": "2026-01-18T11:30:00"
            },
            {
                "roomId": "677def456789012345abc678",
                "title": "제목 없음",
                "createdAt": "2026-01-15T14:00:00",
                "updatedAt": "2026-01-15T14:30:00"
            }
        ]
    }
}
```

**응답 필드 설명**:
- `userId`: 사용자 ID
- `totalRooms`: 총 채팅방 개수
- `rooms`: 채팅방 목록 배열
  - `roomId`: 채팅방 고유 ID
  - `title`: 채팅방 제목 (없으면 "제목 없음")
  - `createdAt`: 생성 시간
  - `updatedAt`: 마지막 업데이트 시간

---

## 7. 채팅방 삭제 (DELETE /api/chat/room)

### 역할
특정 채팅방과 해당 채팅방의 모든 대화 로그를 삭제합니다. 본인 소유의 채팅방만 삭제 가능합니다.

### 요청 설정
- **Method**: `DELETE`
- **URL**: `/api/chat/room?roomId=<ROOM_ID>`
- **인증**: 필수 (`Authorization: Bearer <JWT_TOKEN>`)
- **Headers**:
  - `Authorization: Bearer <JWT_TOKEN>`
- **Query Parameters**:
  - `roomId` (필수): 삭제할 대화방 ID (24자리 MongoDB ObjectId)

### 예상 응답 (200 OK)
```json
{
    "timestamp": "2026-01-23T12:34:56.789Z",
    "path": "/api/chat/room?roomId=679234abc123def456789012",
    "status": 200,
    "error": "OK",
    "requestId": "abc-123-def-456",
    "data": {
        "roomId": "679234abc123def456789012",
        "deletedMessages": 15,
        "message": "채팅방이 성공적으로 삭제되었습니다."
    }
}
```

**응답 필드 설명**:
- `roomId`: 삭제된 채팅방 ID
- `deletedMessages`: 함께 삭제된 채팅 메시지 개수
- `message`: 성공 메시지

### 에러 응답
- **404 Not Found**: 채팅방을 찾을 수 없음
- **403 Forbidden**: 삭제 권한 없음 (다른 사용자의 채팅방)

---

## 8. 이력서 생성 엔드포인트 (POST /api/cv_generation)

### 역할
외국인 근로자의 정보를 입력받아 GPT 기반으로 한국어 이력서(자기소개서)를 자동 생성합니다.

### 요청 설정
- **Method**: `POST`
- **URL**: `/api/cv_generation`
- **인증**: 필수 (`Authorization: Bearer <JWT_TOKEN>`)
- **Headers**:
  - `Content-Type: application/json`
  - `Authorization: Bearer <JWT_TOKEN>`

### Request Body
```json
{
    "personal": {
        "name": "응우옌 반 A",
        "nationality": "베트남",
        "visa": "E-7"
    },
    "experience": [
        {"work": "베트남 하노이 소재 IT 회사에서 3년간 웹 개발 업무 수행"},
        {"work": "Python, Django 프레임워크를 활용한 백엔드 개발"}
    ],
    "language": {
        "korean": "중",
        "others": [
            {"name": "영어", "level": "상"},
            {"name": "베트남어", "level": "상"}
        ]
    },
    "skills": ["Python", "Django", "PostgreSQL"],
    "strengths": ["빠른 학습 능력", "팀워크"],
    "desired_position": "백엔드 개발자"
}
```

**필드 설명**:
- `personal` (필수): 이름, 국적, 비자 정보
- `experience` (필수): 경력 사항 배열
- `language` (필수): 한국어 수준 및 기타 언어
- `skills` (필수): 보유 기술 배열
- `strengths` (필수): 강점 배열
- `desired_position` (선택): 희망 직무

### 예상 응답 (200 OK)
```json
{
    "timestamp": "2026-01-19T12:34:56.789Z",
    "path": "/api/cv_generation",
    "status": 200,
    "error": "OK",
    "requestId": "abc-123-def-456",
    "data": {
        "resume": {
            "introduction": "3년 경력의 베트남 출신 백엔드 개발자로, Python과 Django를 활용한 웹 개발 전문가입니다.",
            "details": "안녕하세요, 응우옌 반 A입니다.\n\n저는 베트남 하노이에서 3년간 IT 업계에서 근무하며..."
        }
    }
}
```

---

## 9. 채팅방 요약 조회 (GET /api/chat/room-summary)

### 역할
특정 채팅방의 저장된 요약 정보를 반환합니다. `roomSummary`가 없는 경우 `null`로 반환되며, 이 경우 `/api/summary`를 먼저 호출해 요약을 생성해야 합니다. `roomId`, `roomTitle`을 함께 반환하므로 요약 목록에서 채팅으로 이동하는 로직에 활용할 수 있습니다.

### 요청 설정
- **Method**: `GET`
- **URL**: `/api/chat/room-summary?roomId=<ROOM_ID>`
- **인증**: 필수 (`Authorization: Bearer <JWT_TOKEN>`)
- **Headers**:
  - `Authorization: Bearer <JWT_TOKEN>`
- **Query Parameters**:
  - `roomId` (필수): 대화방 ID (24자리 MongoDB ObjectId)

### 예상 응답 (200 OK)
```json
{
    "timestamp": "2026-01-19T12:34:56.789Z",
    "path": "/api/chat/room-summary?roomId=abc123",
    "status": 200,
    "error": "OK",
    "requestId": "abc-123-def-456",
    "data": {
        "roomId": "abc123",
        "roomTitle": "E-7 비자 신청 절차 문의",
        "roomSummary": "E-7 비자 신청 절차와 필요 서류에 대한 상담",
        "updatedAt": "2026-01-19T12:34:56.789Z"
    }
}
```

**응답 필드 설명**:
- `roomId`: 채팅방 고유 ID
- `roomTitle`: 채팅방 제목 (없으면 `null`)
- `roomSummary`: 저장된 요약 내용 (`/api/summary` 미호출 시 `null`)
- `updatedAt`: 마지막 대화 저장 시각

### 에러 응답
- **400 Bad Request**: `roomId` 미전달
- **404 Not Found**: 채팅방을 찾을 수 없음 (존재하지 않거나 본인 소유가 아닌 경우)

---

## 빠른 테스트 체크리스트

### ✅ 1단계: 기본 연결 확인
```
GET /api/get_echo_call?param=hello
→ 200 OK 응답 확인
```

### ✅ 2단계: 채팅 스트리밍 확인 (인증 필요)
```
POST /api/chat/ask?roomId=<ROOM_ID>
Headers: Authorization: Bearer <JWT_TOKEN>
Body: {"query": "E-7 비자 신청 방법이 뭔가요?"}
→ 200 OK + SSE 스트리밍 확인
→ 완료 후 roomTitle 자동 생성 여부 확인 (user-rooms 조회)
```

### ✅ 3단계: 대화 요약 확인 (인증 필요)
```
POST /api/summary?roomId=<ROOM_ID>
Headers: Authorization: Bearer <JWT_TOKEN>
→ 200 OK + summary 필드 확인
→ 생성된 요약이 DB에 저장됨 (user-summaries 조회 시 확인)
```

### ✅ 6단계: 마이페이지 상담 요약 목록 조회 (인증 필요)
```
GET /api/chat/user-summaries
Headers: Authorization: Bearer <JWT_TOKEN>
→ 200 OK + summaries 배열, roomTitle / roomSummary 필드 확인
```

### ✅ 9단계: 특정 채팅방 요약 조회 (인증 필요)
```
GET /api/chat/room-summary?roomId=<ROOM_ID>
Headers: Authorization: Bearer <JWT_TOKEN>
→ 200 OK + roomId, roomTitle, roomSummary, updatedAt 필드 확인
→ roomSummary가 null이면 /api/summary 먼저 호출 후 재조회
```

### ✅ 4단계: 대화방 로그 조회 (인증 필요)
```
GET /api/chat/room-log?roomId=<ROOM_ID>
Headers: Authorization: Bearer <JWT_TOKEN>
→ 200 OK + roomId, roomTitle, chatLogs 필드 확인
```

### ✅ 5단계: 최근 대화방 조회 (인증 필요)
```
GET /api/chat/recent-room
Headers: Authorization: Bearer <JWT_TOKEN>
→ 200 OK + roomId, roomTitle, chatLogs 필드 확인
```

### ✅ 6단계: 채팅방 목록 조회 (인증 필요)
```
GET /api/chat/rooms
Headers: Authorization: Bearer <JWT_TOKEN>
→ 200 OK + userId, totalRooms, rooms 배열 확인
```

### ✅ 7단계: 채팅방 삭제 (인증 필요)
```
DELETE /api/chat/room?roomId=<ROOM_ID>
Headers: Authorization: Bearer <JWT_TOKEN>
→ 200 OK + roomId, deletedMessages 필드 확인
```

### ✅ 8단계: 이력서 생성 확인 (인증 필요)
```
POST /api/cv_generation
Headers: Authorization: Bearer <JWT_TOKEN>
Body: 완전한 이력서 데이터 (위 명세 참조)
→ 200 OK + resume.introduction, resume.details 필드 확인
```

---

## 문제 해결 가이드

### 401 Unauthorized
- Authorization 헤더 확인 (`Bearer <JWT_TOKEN>` 형식)
- JWT 토큰 만료 여부 확인
- JWT_SECRET_KEY 환경 변수 설정 확인

### 403 Forbidden
- 대화방 접근 권한 확인 (본인 대화방만 조회/삭제 가능)
- roomId와 사용자 매칭 확인

### 404 Not Found
- URL 경로 확인 (`/api/` 접두사 필수)
- Method 확인 (GET/POST/DELETE)
- 엔드포인트 이름 확인 (`get_echo_call`, `chat/ask`, `chat/rooms` 등)
- 채팅방 삭제 시: 존재하지 않는 roomId

### 400 Bad Request
- Content-Type 헤더 확인 (`application/json`)
- JSON 형식 유효성 검사
- 필수 필드 포함 여부 확인
  - `/api/chat/ask`: `query` 필드, `roomId` 파라미터
  - `/api/summary`: `roomId` 파라미터
  - `/api/chat/room-log`: `roomId` 파라미터
  - `/api/chat/room-summary`: `roomId` 파라미터
  - `/api/chat/room` (DELETE): `roomId` 파라미터
  - `/api/cv_generation`: `personal`, `experience`, `language` 필드

### 500 Internal Server Error
- Azure Portal에서 로그 확인
- 환경 변수 설정 확인:
  - `RAG_DATA_MONGODB_URI` (RAG 검색용 MongoDB)
  - `LOG_DATA_MONGODB_URI` (대화 로그용 MongoDB)
  - `OPENAI_API_KEY`
  - `JWT_SECRET_KEY`
- 패키지 설치 확인 (`requirements.txt`)
- MongoDB 연결 상태 확인

### SSE 스트리밍 오류
- 클라이언트가 SSE를 지원하는지 확인
- `text/event-stream` MIME 타입 처리 확인
- 네트워크 타임아웃 설정 확인

---

## API 엔드포인트 요약표

| # | Method | Endpoint | 인증 | 필수 파라미터 | 설명 |
|---|--------|----------|------|---------------|------|
| 1 | GET | `/api/get_echo_call` | ❌ | `param` (query) | 테스트 에코 |
| 2 | POST | `/api/chat/ask` | ✅ | `roomId` (query), `query` (body) | 채팅 스트리밍 |
| 3 | POST | `/api/summary` | ✅ | `roomId` (query) | 대화 요약 |
| 4 | GET | `/api/chat/room-log` | ✅ | `roomId` (query) | 대화방 로그 |
| 5 | GET | `/api/chat/recent-room` | ✅ | - | 최근 대화방 |
| 6 | GET | `/api/chat/rooms` | ✅ | - | 채팅방 목록 |
| 7 | DELETE | `/api/chat/room` | ✅ | `roomId` (query) | 채팅방 삭제 |
| 8 | POST | `/api/cv_generation` | ✅ | `personal`, `experience`, `language` (body) | 이력서 생성 |
| 9 | GET | `/api/chat/room-summary` | ✅ | `roomId` (query) | 채팅방 요약 조회 |
