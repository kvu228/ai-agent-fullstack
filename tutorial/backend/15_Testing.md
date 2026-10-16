# 15 · Testing: không cần Docker, không cần OpenAI

## 🎯 Mục tiêu

- Chạy toàn bộ test backend **trong vài giây**, không Docker, không mạng, không tốn tiền API.
- Thay từng hạ tầng bằng bản giả đúng chỗ: SQLite, moto (S3), Chroma in-memory, embedding và LLM giả, checkpointer in-memory.
- Test qua HTTP thật với `httpx.AsyncClient` + `ASGITransport`.
- Viết test cho **bảo mật** (cách ly user, CSRF, reuse token) chứ không chỉ "happy path".
- Biết giới hạn của bộ test này và khi nào cần test end-to-end với hạ tầng thật.

## 📁 File liên quan

- `backend/tests/conftest.py` — fixtures
- `backend/tests/test_auth.py`, `test_documents.py`, `test_chat.py`, `test_unit.py`
- `backend/pyproject.toml` (`[tool.pytest.ini_options]`, `[tool.mypy]`)

---

## 1. Chiến lược: thay gì bằng gì

| Production | Trong test | Vì sao được |
| --- | --- | --- |
| PostgreSQL | SQLite file trong `tmp_path` (`aiosqlite`) + `create_all` | Code dùng SQLAlchemy chuẩn, không SQL riêng của Postgres |
| S3 (Floci/AWS) | `moto` `mock_aws()` | Chặn boto3 ở tầng HTTP, giả lập S3 trong RAM |
| Chroma server | `chromadb.EphemeralClient()` | Cùng API, chạy in-process |
| OpenAI embeddings | `DeterministicFakeEmbedding(size=64)` | Cùng text → cùng vector, không gọi mạng |
| OpenAI chat | `FakeToolCallingModel` | Trả các `AIMessage` soạn sẵn |
| `AsyncPostgresSaver` | `InMemorySaver` | Cùng interface checkpointer |

Đánh đổi: SQLite không phải Postgres (ví dụ datetime không có múi giờ → `_as_utc`, bài 10). Những khác biệt kiểu này được bù bằng test end-to-end (mục 6).

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

`asyncio_mode = "auto"` (pytest-asyncio): mọi `async def test_...` và async fixture tự chạy trong event loop, không cần decorator.

## 2. Settings cho test

```python
@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]  # ignore the developer's .env
        environment="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        s3_endpoint_url=None,
        s3_bucket="test-documents",
        chunk_size=200,
        chunk_overlap=20,
        max_upload_mb=1,
        cors_origins=["http://localhost:3000"],
    )
```

- `_env_file=None`: test **không** đọc `.env` của bạn — kết quả giống nhau trên mọi máy và CI.
- `s3_endpoint_url=None` → boto3 "gọi AWS thật", moto bắt lại.
- `max_upload_mb=1`, `chunk_size=200`: giá trị nhỏ để test nhanh và dễ chạm giới hạn.

## 3. Fixture `app`: lắp app như lifespan, nhưng bằng đồ giả

```python
    # NullPool: no connection outlives a test (SQLite + async + pooling don't mix well).
    engine = create_async_engine(settings.database_url, poolclass=pool.NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    ...
    chroma_client = chromadb.EphemeralClient()
    vector_store = DocumentVectorStore(
        chroma_client,
        DeterministicFakeEmbedding(size=64),
        collection_name=f"test-{uuid.uuid4().hex}",  # isolated per test
    )

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    # Same attributes the real lifespan sets (the lifespan itself never runs in tests).
    app.state.session_factory = session_factory
    app.state.storage = storage
    ...
    app.state.graph = build_agent_graph(
        fake_llm, get_tools(vector_store, k=3), checkpointer=InMemorySaver()
    )
```

- Mỗi test có DB mới (`tmp_path` khác nhau), bucket mới (moto reset), collection Chroma tên ngẫu nhiên (`EphemeralClient` dùng chung bộ nhớ trong một process) → **không test nào ảnh hưởng test nào**.
- `create_all` ở đây là hợp lý (DB dùng một lần); production luôn dùng Alembic (bài 08).
- `httpx.ASGITransport` **không** chạy lifespan → ta tự gán đúng các thuộc tính `app.state` mà lifespan gán (bài 04). Đó là lợi ích của việc endpoint chỉ đọc tài nguyên qua dependency.

## 4. LLM giả

```python
class FakeToolCallingModel(GenericFakeChatModel):
    """GenericFakeChatModel + a no-op `bind_tools`, so it fits our agent node.

    Streaming is disabled because the fake's streaming drops `tool_calls`;
    each reply is then emitted as a single "token" event.
    """

    disable_streaming: bool = True

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "FakeToolCallingModel":
        return self
```

Test "kịch bản hoá" câu trả lời: `fake_llm.messages = iter([AIMessage(..., tool_calls=[...]), AIMessage("Answer")])` → lượt 1 agent gọi `search_documents`, lượt 2 trả lời. Nhờ vậy test được cả vòng ReAct mà không cần OpenAI. Chi tiết test agent: [agent/12](../agent/12_TestingAgents.md).

## 5. Client HTTP và các test

```python
@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c
```

Request đi qua toàn bộ stack (middleware, dependency, exception handler) như thật, chỉ là không qua socket. `AsyncClient` giữ cookie như trình duyệt → test được luồng cookie. `make_client()` tạo client thứ hai cho test nhiều user; `register_and_login(client, email)` là helper dùng chung.

```python
async def test_upload_is_ingested(client: AsyncClient) -> None:
    await register_and_login(client)
    response = await upload(client)
    assert response.status_code == 202
    document_id = response.json()["id"]

    # Background ingestion has finished once the ASGI call returns.
    document = (await client.get(f"/api/v1/documents/{document_id}")).json()
    assert document["status"] == "ready", document
    assert "s3_key" not in document
```

Với `ASGITransport`, `BackgroundTasks` chạy xong **trước** khi lời gọi trả về → không cần polling/sleep trong test.

| File | Kiểm tra gì |
| --- | --- |
| `test_auth.py` (10) | register/login/me, email trùng 409, mật khẩu ngắn 422, sai mật khẩu ≡ email lạ, bearer không cookie, token bị sửa, rotation + reuse, logout, CSRF 403 |
| `test_documents.py` (7) | upload → `ready`, `.exe` 422, quá lớn 413, path traversal, presigned URL, **cách ly user** 404, xoá |
| `test_chat.py` (5) | agent tìm trong tài liệu của mình, **không bao giờ** thấy tài liệu người khác, conversation lưu & liệt kê, không dùng được conversation của người khác, xoá |
| `test_unit.py` (7) | hash mật khẩu, JWT roundtrip/hết hạn/sai khoá, production cần secret thật, loader |

Tổng cộng **29 test, ~4 giây**. Gần một nửa là test **bảo mật** — những thứ dễ hỏng âm thầm nhất khi refactor.

```bash
make test                       # backend + frontend
cd backend && uv run pytest -v  # chỉ backend
uv run pytest -k reuse          # lọc theo tên
make lint                       # ruff check + ruff format --check + mypy app scripts tests (strict)
```

## 6. Test end-to-end với hạ tầng thật (tuỳ chọn)

Unit/integration test ở trên không bắt được: sai `DATABASE_URL`, khác biệt Postgres/SQLite, Floci/Chroma thật, header qua proxy Next.js (bài 13). Cách làm khi phát triển project này: một script riêng (không commit) monkeypatch `app.main.get_chat_model` và `app.main.get_embeddings` thành bản giả, rồi chạy server thật với `PYTHONPATH=.` trên `make infra-up` + `make migrate`. Có LLM giả nên không tốn tiền, nhưng mọi thứ khác là thật. Sau đó dùng `curl` hoặc trình duyệt để kiểm luồng đầy đủ.

## ⚠️ Lỗi thường gặp

- **Test đọc `.env` của máy dev** → pass trên máy bạn, fail trên CI. Luôn `_env_file=None`.
- **Dùng chung một collection Chroma/bucket giữa các test** → test phụ thuộc thứ tự chạy.
- **Mock quá sâu** (mock `DocumentService` khi test endpoint documents) → test pass dù code hỏng. Ưu tiên giả lập *hạ tầng*, giữ nguyên code của mình.
- **Quên `AWS_DEFAULT_REGION`** → boto3 báo `NoRegionError` dưới moto.
- **Thêm test gọi OpenAI thật** → chậm, tốn tiền, không ổn định. Đánh dấu riêng (`@pytest.mark.live`) và không chạy mặc định nếu thật sự cần.

## 🧪 Thực hành

1. Viết test: user A upload, user B gọi `GET /documents/{id}/download` → 404.
2. Viết test cho `/health/ready` khi Chroma lỗi: thay `app.state.chroma_client` bằng object có `heartbeat()` ném exception, assert 503 và `checks["vector_store"] is False`.
3. Chạy `uv run pytest --durations=5` để xem test nào chậm nhất và vì sao.

---

⬅️ Trước: [14 · Error Handling & Health Checks](14_ErrorHandlingAndHealthChecks.md) · ➡️ Tiếp: [16 · Docker & Production](16_DockerAndProduction.md)
