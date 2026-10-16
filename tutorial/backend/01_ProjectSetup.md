# 01 · Project Setup với uv

## 🎯 Mục tiêu

- Hiểu vì sao dùng **uv** để quản lý Python project.
- Đọc hiểu `pyproject.toml`: dependencies (web, database, storage, vector store, auth, AI) và dev dependencies.
- Nắm cấu trúc thư mục `backend/` và lý do chia layer như vậy.
- Chạy được server dev và mở trang tài liệu `/docs`.

## 📁 File liên quan

- `backend/pyproject.toml`, `backend/uv.lock`, `backend/.python-version`
- `backend/app/` (toàn bộ source)
- `Makefile` (ở root)

---

## 1. Tại sao là uv?

[uv](https://docs.astral.sh/uv/) thay thế cùng lúc `pip`, `venv`, `pip-tools`, `pyenv`:

| Việc cần làm | Cách cũ | Với uv |
| --- | --- | --- |
| Tạo virtualenv | `python -m venv .venv` | tự động |
| Cài package | `pip install fastapi` | `uv add fastapi` |
| Khóa phiên bản | `pip freeze > requirements.txt` | `uv.lock` (tự sinh) |
| Chạy lệnh trong venv | `source .venv/bin/activate && ...` | `uv run ...` |

Hai điểm quan trọng nhất:

1. **Lock file (`uv.lock`)** ghi lại *chính xác* phiên bản của mọi package (kể cả package gián tiếp). `uv sync` trên máy khác — hay trong Docker (bài 16, `uv sync --locked`) — cho ra môi trường giống hệt.
2. **`uv run`** luôn chạy trong đúng venv của project, không cần `activate`.

## 2. Đọc `pyproject.toml`

```toml
[project]
name = "agent-backend"
requires-python = ">=3.12"
dependencies = [
    "alembic>=1.20.0",
    "boto3>=1.43.114",
    "chromadb>=1.5.9",
    "fastapi[standard]>=0.143.2",
    "langchain>=1.4.6",
    "langchain-anthropic>=1.7.6",
    "langchain-chroma>=1.1.0",
    "langchain-openai>=1.7.2",
    "langchain-text-splitters>=1.1.3",
    "langgraph>=1.2.15",
    "langgraph-checkpoint-postgres>=3.2.0",
    "psycopg[binary,pool]>=3.3.6",
    "pwdlib[argon2]>=0.3.1",
    "pydantic-settings>=2.16.0",
    "pyjwt>=2.15.1",
    "pypdf>=6.20.1",
    "sqlalchemy[asyncio]>=2.1.4",
]
```

Nhóm theo vai trò:

| Vai trò | Package | Học ở bài |
| --- | --- | --- |
| Web framework | `fastapi[standard]` (kèm uvicorn, `fastapi` CLI) | 04, 05 |
| Config | `pydantic-settings` | 02 |
| Database (PostgreSQL) | `sqlalchemy[asyncio]`, `psycopg[binary,pool]`, `alembic` | 07, 08 |
| Auth | `pyjwt` (JWT), `pwdlib[argon2]` (hash mật khẩu) | 10 |
| Blob storage (S3) | `boto3` | 12 |
| Vector store | `chromadb`, `langchain-chroma` | [agent/04](../agent/04_EmbeddingsAndVectorStore.md) |
| Đọc & cắt tài liệu | `pypdf`, `langchain-text-splitters` | [agent/03](../agent/03_DocumentLoadersAndChunking.md) |
| Agent | `langchain`, `langgraph`, `langchain-anthropic`, `langchain-openai` | agent/01–13 |
| Bộ nhớ hội thoại | `langgraph-checkpoint-postgres` | [agent/10](../agent/10_MemoryPostgresCheckpointer.md) |

- `psycopg[binary,pool]`: driver PostgreSQL v3 (hỗ trợ async thật), `binary` = bản build sẵn, `pool` = connection pool cho checkpointer của LangGraph.
- `sqlalchemy[asyncio]`: kéo theo `greenlet`, cần cho `AsyncSession`.

```toml
[dependency-groups]
dev = [
    "aiosqlite>=0.22.1",
    "httpx>=0.28.1",
    "moto[s3]>=5.2.4",
    "mypy>=2.4.0",
    "pytest>=9.1.1",
    "pytest-asyncio>=1.4.0",
    "ruff>=0.17.1",
]
```

**Dev dependencies** chỉ dùng khi phát triển, không vào image production (`uv sync --no-dev`). Ở đây có thêm `aiosqlite` (SQLite async thay Postgres khi test) và `moto` (giả lập S3 trong process) — bài 15 giải thích vì sao test không cần Docker.

```toml
# This is an application, not a library: no need to build/install it as a package.
[tool.uv]
package = false
```

Backend là **ứng dụng**, không phải thư viện → không cần build system. `uv run` chạy từ `backend/` nên `import app...` hoạt động trực tiếp.

### Công cụ chất lượng code

```toml
[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM", "ASYNC"]

[tool.pytest.ini_options]
asyncio_mode = "auto"

[tool.mypy]
strict = true
```

- **ruff** = linter + formatter. Rule `ASYNC` đặc biệt hữu ích ở project này: nó cảnh báo khi gọi hàm *blocking* quen thuộc (`time.sleep`, `open`, `urllib`/`requests`…) trong `async def`. Nó **không** nhận ra mọi thư viện sync — boto3 chẳng hạn — nên ta vẫn phải tự nhớ bọc bằng `asyncio.to_thread` (bài 12).
- `asyncio_mode = "auto"`: test `async def` chạy được mà không cần decorator.
- `mypy --strict`: bắt buộc type hint đầy đủ.

## 3. Cấu trúc thư mục

```
backend/
├── app/
│   ├── main.py              # create_app() + lifespan                     (bài 04)
│   ├── core/                # config (02), security (10), exceptions + logging (14)
│   ├── db/                  # base.py (Base, mixins), session.py (engine) (bài 07)
│   ├── models/              # User, RefreshToken, Document, Conversation  (bài 07)
│   ├── schemas/             # Pydantic request/response                   (bài 06)
│   ├── api/
│   │   ├── deps.py          # Dependency Injection, auth, CSRF            (bài 09, 10)
│   │   └── v1/endpoints/    # health, auth, documents, chat               (bài 05)
│   ├── services/            # auth_service, document_service, chat_service (bài 11)
│   ├── storage/s3.py        # S3Storage                                   (bài 12)
│   ├── vectorstore/         # DocumentVectorStore (Chroma)                (agent/04)
│   ├── ingestion/           # loaders + pipeline                          (agent/03, 05)
│   └── agents/              # LangChain/LangGraph                         (agent/*)
├── migrations/              # Alembic                                     (bài 08)
├── scripts/run_agent.py     # chat với agent trong terminal
├── tests/                   # pytest                                      (bài 15)
├── alembic.ini, Dockerfile, .dockerignore, .env.example
```

Nguyên tắc: **phụ thuộc chỉ đi một chiều** `api → services → (models, storage, vectorstore, agents) → core`. Tầng dưới không bao giờ import tầng trên — ví dụ `storage/s3.py` không biết FastAPI là gì, nên dùng lại được trong script hay worker.

## 4. Chạy thử

```bash
cd backend
uv sync                          # tạo .venv và cài đúng theo uv.lock
cp .env.example .env             # điền API key (xem bài 02)
```

Backend cần Postgres, S3 (Floci) và Chroma đang chạy — bài 03 dựng chúng bằng Docker. Từ root:

```bash
make infra-up        # docker compose up -d --wait postgres floci chroma
make migrate         # tạo bảng (bài 08)
make dev-backend     # = cd backend && uv run fastapi dev app/main.py
```

Mở http://localhost:8000/docs để xem Swagger UI. Các lệnh hằng ngày:

```bash
uv add <pkg>             # thêm dependency (cập nhật pyproject + uv.lock)
uv add --dev <pkg>       # thêm dev dependency
uv run pytest            # chạy test
uv run ruff check . && uv run ruff format .
uv run mypy app scripts tests
```

## ⚠️ Lỗi thường gặp

- **Sửa tay `uv.lock`** → đừng. Luôn dùng `uv add` / `uv remove` / `uv lock`.
- **`pip install` vào venv** → package không được ghi vào `pyproject.toml`, người khác clone về sẽ thiếu.
- **`ModuleNotFoundError: app`** → bạn chạy lệnh ở root thay vì trong `backend/`.
- **Server không khởi động vì `Connection refused` tới 5433/4566/8001** → hạ tầng chưa chạy, xem bài 03.

## 🧪 Thực hành

1. Chạy `uv tree --depth 1` để xem cây dependency trực tiếp.
2. Mở `uv.lock`, tìm `psycopg` và quan sát các package nó kéo theo.
3. Chạy `uv run ruff check --select ASYNC .` — hiện tại không có lỗi. Thêm tạm `time.sleep(1)` vào một hàm `async def` bất kỳ rồi chạy lại để thấy ruff báo lỗi; sau đó thử `boto3.client("s3").list_buckets()` và nhận ra ruff *không* bắt được — lý do bài 12 phải dùng `asyncio.to_thread`.

---

⬅️ Trước: [00 · Tổng quan](../00_Overview.md) · ➡️ Tiếp: [02 · Configuration](02_Configuration.md)
