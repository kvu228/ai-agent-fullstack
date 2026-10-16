# AI Agent Fullstack — Document Assistant

A learning project: a **document assistant** (RAG agent) built with **LangChain / LangGraph**, served by **FastAPI**, with a **Next.js + TypeScript** frontend. Users sign up, upload PDF/TXT/Markdown files, and chat with an agent that searches *their own* documents and cites the sources.

| Folder | Stack |
|---|---|
| [`backend/`](backend) | Python 3.12, FastAPI, LangGraph, SQLAlchemy 2 (async) + Alembic, boto3, ChromaDB, uv, pytest, ruff, mypy |
| [`frontend/`](frontend) | Next.js 16 (App Router), React 19, TypeScript, Tailwind CSS 4, Vitest |
| [`tutorial/`](tutorial) | Step-by-step tutorials (Vietnamese) — start at [`tutorial/00_Overview.md`](tutorial/00_Overview.md) |

## Architecture

```
Browser ──► Next.js :3000 ──(/api/* rewrite)──► FastAPI :8000
                                                  │
              ┌──────────────────┬────────────────┼───────────────────┐
              ▼                  ▼                ▼                   ▼
        PostgreSQL :5433    S3 (Floci) :4566   ChromaDB :8001     LLM provider
        users, tokens,      original files     chunk embeddings   (Anthropic/OpenAI)
        documents, chats,   (presigned URLs)   filtered by user
        LangGraph checkpoints
```

| Local (docker compose) | Production equivalent |
|---|---|
| `postgres:17` | AWS RDS for PostgreSQL |
| `floci` (free AWS emulator) | AWS S3 |
| `chromadb/chroma` | Self-hosted Chroma or Chroma Cloud |

Auth: email + password (Argon2id), short-lived JWT access token + rotating refresh token, both in httpOnly cookies (Bearer header also accepted for API clients).

## Quick start (local development)

```bash
make install
cp backend/.env.example backend/.env            # add ANTHROPIC_API_KEY and OPENAI_API_KEY
cp frontend/.env.example frontend/.env.local

make infra-up       # Postgres, Floci (S3), ChromaDB
make migrate        # create the tables
make dev-backend    # http://localhost:8000/docs
make dev-frontend   # http://localhost:3000
```

> The default embedding model is `openai:text-embedding-3-small` (needs `OPENAI_API_KEY`), the chat model is `anthropic:claude-sonnet-5-5` (needs `ANTHROPIC_API_KEY`). Change `EMBEDDING_MODEL` / `LLM_MODEL` in `backend/.env` to use other providers.

Run everything in Docker instead:

```bash
make up             # infra + migrations + backend + frontend → http://localhost:3000
make down
```

```bash
make test           # pytest + vitest (no Docker, no API key: fakes for LLM, S3, DB, Chroma)
make lint           # ruff, mypy, eslint, tsc
```

## API

All paths are prefixed with `/api/v1`. 🔒 = requires authentication.

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Liveness (process is up) |
| GET | `/health/ready` | Readiness (database, S3, Chroma reachable) — 503 if not |
| POST | `/auth/register` | Create an account |
| POST | `/auth/login` | Log in → sets cookies, returns access token |
| POST | `/auth/refresh` | Rotate the refresh token, issue a new access token |
| POST | `/auth/logout` | Revoke the refresh token, clear cookies |
| GET | `/auth/me` 🔒 | Current user |
| POST | `/documents` 🔒 | Upload a file (202, ingested in the background) |
| GET | `/documents` 🔒 | List my documents |
| GET | `/documents/{id}` 🔒 | One document (status: processing / ready / failed) |
| GET | `/documents/{id}/download` 🔒 | Presigned S3 download URL |
| DELETE | `/documents/{id}` 🔒 | Delete file, vectors and metadata |
| POST | `/chat/stream` 🔒 | Ask a question, stream the answer (SSE) |
| GET | `/conversations` 🔒 | List my conversations |
| GET | `/conversations/{id}/messages` 🔒 | Conversation history |
| DELETE | `/conversations/{id}` 🔒 | Delete a conversation |
