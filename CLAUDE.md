# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

FastAPI-based AI chatbot backend for foreign workers seeking Korean visa/employment legal information. Deployed on Azure (Container Apps + Functions). The primary entry point is `main.py` (FastAPI); `function_app.py` is a legacy Azure Functions wrapper kept for compatibility.

## Development Commands

```bash
# Run locally with Azure Functions Core Tools (recommended per README)
npm install -g azure-functions-core-tools@4 --unsafe-perm true
func start
# Wait for: "Worker process started and initialized."

# Run directly with uvicorn (alternative)
uvicorn main:app --reload  # dev on port 8000

# Docker build and run
docker build -t helloworld-ai-azure .
docker run -p 80:8000 helloworld-ai-azure

# Manual endpoint testing
python test_question_endpoint.py
```

**Test via Postman** — the team uses a shared Postman workspace; no automated test suite exists.

## Required Environment Variables

Copy `.env.example` to `.env` and populate:
- `RAG_DATA_MONGODB_URI` — MongoDB Atlas for the knowledge base (RAG search)
- `LOG_DATA_MONGODB_URI` — MongoDB Atlas for chat logs and rooms
- `OPENAI_API_KEY` — GPT-4o-mini API access
- `JWT_SECRET_KEY` — JWT signing key for auth

## Architecture

### Entry Points
- **`main.py`** — All FastAPI routes and `ChatService` class (~1350 lines). Active production code.
- **`function_app.py`** — Azure Functions wrapper; routes map to the same logic.

### Core Classes
- **`ChatService`** (`main.py`) — Singleton initialized at startup. Manages both MongoDB clients (async `motor` + sync `pymongo`), orchestrates `ChatModel` and `TranslateModel`, handles chat history/rooms.
- **`ChatModel`** (`query_model.py`) — Hybrid RAG search + GPT response generation. Two-stage search: MongoDB text search on `title`/`contents`, then vector ANN search on `Embedding` field (OpenAI embeddings). Supports both streaming and non-streaming.
- **`TranslateModel`** (`translate_model.py`) — Translates queries to Korean; generates MongoDB aggregation pipelines for structured queries.

### Database Structure
- **RAG DB** (`HelloWorld-AI` → `foreigner_legalQA_v3`): fields `title`, `contents`, `url`, `Embedding`; has `vector_index` for ANN search.
- **Log DB** (`chatdb`): `chat` collection (messages), `rooms` collection (room metadata).

### Concurrency Pattern
- Async MongoDB (`motor`) for most operations; sync `pymongo` for streaming routes (avoids event loop conflicts during SSE).
- Semaphore limits concurrent OpenAI requests to 100.
- Connection pools: maxPoolSize=50, minPoolSize=10.

## API Endpoints

All routes are under `/api/`. Most require `Authorization: Bearer <JWT_TOKEN>`.

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/get_echo_call?param=<val>` | No | Health/echo check |
| POST | `/api/question` | Yes | Non-streaming RAG Q&A (legacy) |
| POST | `/api/chat/ask?roomId=<id>` | Yes | SSE streaming chat with history |
| POST | `/api/summary?roomId=<id>` | Yes | One-line room summary |
| GET | `/api/chat/room-log?roomId=<id>` | Yes | Full conversation log |
| GET | `/api/chat/recent-room` | Yes | Latest room + logs |
| POST | `/api/chat/create-room` | Yes | Create new chat room |
| GET | `/api/chat/user-rooms` | Yes | List user's rooms |
| DELETE | `/api/chat/user-rooms` | Yes | Delete all user rooms and logs |
| DELETE | `/api/chat/room?roomId=<id>` | Yes | Delete specific room and logs |
| POST | `/api/cv_generation` | Yes | Generate Korean resume from profile |

### Standard Response Format
```json
{
    "timestamp": "ISO8601",
    "path": "/api/endpoint",
    "status": 200,
    "error": "OK",
    "requestId": "uuid",
    "data": {}
}
```

### Streaming (SSE) Format — `/api/chat/ask`
```
data: {"type":"content","content":"chunk"}
```
Chat history (last 10 messages) is auto-loaded from MongoDB by `roomId`; logs are auto-saved after stream completes.

## Branch and Deployment Workflow

- **Always test locally** (`func start`) before pushing to `develop`.
- Feature branches: `feat/<name>` → merge into `develop` → merge into `main`.
- CI/CD via GitHub Actions (`.github/workflows/`) auto-deploys to Azure Container Apps.
- Production base URL: `http://helloworld-func-app-v2-e2gad2h8gwdbatha.koreacentral-01.azurewebsites.net`

## Key Config

`configs/config.json` controls model selection and MongoDB collection names:
- Generate model: `gpt-4o-mini`
- Embedding model: `text-embedding-3-large`
- RAG `top_k`: 20, `temperature`: 0.7
- Resume generation `temperature`: 0 (deterministic)

`prompts/prompts.py` contains all prompt templates (~18K lines).
