# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A learning project: a RAG document assistant. Users upload PDF/TXT/Markdown files and chat with a LangGraph agent that searches **their own** documents and cites the sources.

- `backend/`: Python 3.12, FastAPI, LangGraph, SQLAlchemy 2 (async) + Alembic, ChromaDB, boto3. Managed with **uv** (`package = false`, so it is not an installable package).
- `frontend/`: Next.js 16 App Router, React 19, TypeScript, Tailwind 4, Vitest.
- `tutorial/`: step-by-step tutorials in Vietnamese that explain the code. If you change code that a tutorial walks through, the tutorial may need the same change. `tutorial/platform/` covers CI and the other items in `todo.md`.
- `docs/adr/`: architecture decision records (`NNNN-title.md`), one per significant decision.

## Commands

The root `Makefile` wraps everything (`make help`):

```bash
make install                  # uv sync + npm install
make infra-up                 # Postgres :5433, Floci (S3 emulator) :4566, Chroma :8001
make migrate                  # alembic upgrade head
make dev-backend              # uv run fastapi dev app/main.py  → :8000/docs
make dev-frontend             # next dev → :3000
make up / make down           # whole stack in Docker (compose profile "app")
make test                     # pytest + vitest  (= test-backend + test-frontend)
make lint                     # ruff check, ruff format --check, mypy (strict), eslint, tsc  (= lint-backend + lint-frontend)
make test-backend PYTEST_ARGS="--cov"   # with coverage, as CI runs it (fails under 80%)
```

### CI

`.github/workflows/ci.yml` runs on every PR and every push to `main`. A `changes` job decides which of backend/frontend to check (pushes to `main` check both); the jobs call the same `make lint-*` / `make test-*` targets you run locally, so change a lint or test command in the `Makefile`, not in the workflow. A `docker` matrix job builds each image and fails on fixable HIGH/CRITICAL vulnerabilities (Trivy). `CI result` is the single check required by branch protection; add any new job to its `needs`. Actions are pinned by commit SHA (Dependabot updates them). `UV_LOCKED=1` is set in CI, so changing `pyproject.toml` without running `uv lock` fails the build.

Single tests:

```bash
cd backend && uv run pytest tests/test_chat.py::test_name -x
cd frontend && npx vitest run src/lib/sse.test.ts
```

Other useful commands:

```bash
cd backend && uv run alembic revision --autogenerate -m "msg"   # new migration
cd backend && uv run python -m scripts.run_agent --email you@example.com  # chat with the agent in the terminal, without FastAPI (needs infra up)
cd frontend && npm run typecheck    # runs `next typegen` first (generates route types such as LayoutProps)
cd frontend && npm run format       # prettier
```

Backend config comes from `backend/.env` (copy it from `.env.example`). Each variable maps to a field of `Settings` in `app/core/config.py`. Models are given as `"<provider>:<model>"` strings (`LLM_MODEL`, `EMBEDDING_MODEL`), and `app/agents/llm.py` is the only place that touches provider SDKs. If you change the embedding model, you have to re-ingest every document.

## Architecture

```
Browser → Next.js :3000 → (/api/* rewrite in next.config.ts) → FastAPI :8000
            Postgres: users, refresh tokens, documents, conversations + LangGraph checkpoints
            S3 (Floci locally): original files
            Chroma: chunk embeddings
```

### Backend

- **Lifespan builds every shared resource** (`app/main.py`): the SQLAlchemy session factory, `S3Storage`, `DocumentVectorStore`, `IngestionPipeline`, and the compiled LangGraph graph with an `AsyncPostgresSaver` checkpointer (a separate psycopg pool). They are stored on `app.state`. `app/api/deps.py` exposes them as `Annotated[..., Depends(...)]` aliases (`DbSession`, `GraphDep`, `CurrentUser`, `ChatServiceDep`, …).
- **Layering**: endpoints (`app/api/v1/endpoints/`) stay thin and call services (`app/services/`). Services raise `AppError` subclasses from `app/core/exceptions.py`, and registered handlers turn them into `{"error": {"message": ...}}` JSON responses.
- **Agent** (`app/agents/`): a ReAct loop `START → agent → (tool_calls? → tools → agent) → END` built with `tools_condition`. The state is only `messages` (with the `add_messages` reducer). The conversation id is the checkpointer `thread_id`, so message history lives in LangGraph checkpoints and **not** in our own tables. `ChatService.messages()` reads it back with `graph.aget_state`.
- **Multi-tenancy invariant**: the user id reaches tools through `AgentContext` (`context=` on `astream`, read as `runtime.context` inside the tool). The LLM never passes it as a tool argument. Every Chroma chunk carries `user_id` metadata, and every search filters on it. New tools and new vector-store queries must keep this rule.
- **Streaming chat**: `ChatService.stream` runs `astream(stream_mode=["messages", "updates"])` and maps the output to `StreamEvent`s: `metadata → (token | tool_start | tool_end)* → done`, or `error`. The 200 status is sent before the stream starts, so any check that should return an HTTP error, such as ownership or a 404, has to run in a dependency (`get_chat_conversation`). Errors that happen during the stream are reported in-band as `error` events.
- **Ingestion**: `POST /documents` stores the file in S3 and returns 202. A FastAPI `BackgroundTasks` job then runs `IngestionPipeline.run`, which opens its own DB session, loads the file (in a thread), splits it, embeds it into Chroma with deterministic ids `"{document_id}:{i}"`, and sets `documents.status` to `ready` or `failed`.
- **Auth**: Argon2id passwords. A 15-minute JWT access token and a rotating, single-use refresh token are both set as httpOnly cookies; a Bearer header is also accepted. Requests authenticated by cookie that change state must pass an `Origin` check against `cors_origins` (CSRF protection).
- **Migrations**: `migrations/env.py` imports `app.models`, so every new model must be exported from `app/models/__init__.py` or autogenerate will not see it. Migration file names include a date prefix (`file_template` in `alembic.ini`). LangGraph creates its own checkpoint tables through `checkpointer.setup()`, so they are not part of the Alembic migrations.

### Backend tests

`tests/conftest.py` replaces every external dependency, so tests need no Docker, no network, and no API keys. Postgres is replaced by SQLite through aiosqlite, S3 by moto, Chroma by an `EphemeralClient` with a fresh collection per test, embeddings by `DeterministicFakeEmbedding`, the checkpointer by `InMemorySaver`, and the LLM by `FakeToolCallingModel`. The lifespan does **not** run in tests: the `app` fixture fills `app.state` by hand, so a new resource added to the lifespan must be added there as well. Tests script LLM replies with `fake_llm.messages = iter([AIMessage(...), ...])`. Use `register_and_login(client)` for an authenticated client and `make_client()` when a test needs several users. `asyncio_mode = "auto"` is set, so async tests need no marker.

### Frontend

- **Read `frontend/AGENTS.md`**: this Next.js version has breaking changes compared with your training data. Check `frontend/node_modules/next/dist/docs/` before you write Next.js code. For example, middleware lives in `src/proxy.ts` (exported as `proxy`), and `next.config.ts` turns on `cacheComponents`.
- **Backend-for-frontend proxy**: the browser only calls the same-origin `/api/v1/...`, and Next.js rewrites those calls to `BACKEND_URL`, so cookies stay first-party and CORS is never involved. `BACKEND_URL` is read at build time.
- `src/proxy.ts` only checks that a session cookie exists, to redirect to `/login`. The real auth check happens in the backend.
- `src/lib/api/client.ts` (`apiFetch`): when a request gets a 401, it calls `/auth/refresh` once and retries. Concurrent 401s share **one** in-flight refresh because refresh tokens are single-use. Keep this behavior if you edit the client.
- Chat uses `fetch` and a custom SSE parser (`src/lib/sse.ts`) because the endpoint is a POST, which `EventSource` can't send. State is handled by `useChat`, which wraps a pure `chatReducer` (`src/hooks/chatReducer.ts`, unit-tested).
- Route groups: `(auth)` holds the login and register pages, and `(app)` holds the logged-in pages wrapped in `AuthProvider`. The `@/` path alias points to `src/`. Vitest only picks up `src/**/*.test.ts`.