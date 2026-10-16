# 12 · Testing Agents (không cần LLM, Docker hay API key)

## 🎯 Mục tiêu

- Test agent RAG **nhanh, miễn phí, tất định** bằng các bản thay thế: fake chat model, fake embeddings, Chroma in-memory.
- Hiểu `FakeToolCallingModel`: vì sao cần `bind_tools` no-op và `disable_streaming=True`.
- Biết "kịch bản hoá" một lượt search → trả lời và kiểm tra **tool thật** chạy trên **vector store thật**.
- Viết test **cách ly giữa user** — test bảo mật quan trọng nhất của RAG nhiều người dùng.

## 📁 File liên quan

- `backend/tests/conftest.py` — fixtures: `FakeToolCallingModel`, `fake_llm`, `app`, `client`, `make_client`
- `backend/tests/test_chat.py` — test qua HTTP (SSE, tool, lịch sử, cách ly)
- `backend/tests/test_documents.py` — upload → ingest → `ready`
- `backend/tests/test_unit.py` — test loader, security thuần

---

## 1. Thay gì bằng gì?

Docstring đầu `backend/tests/conftest.py`:

```text
    Postgres  -> SQLite file (aiosqlite)          LLM        -> scripted fake chat model
    S3        -> moto (in-process AWS mock)       embeddings -> DeterministicFakeEmbedding
    Chroma    -> in-memory EphemeralClient        checkpoint -> InMemorySaver

So `uv run pytest` needs no Docker, no network and no API key.
```

Ta không test "LLM có thông minh không" (đó là *evaluation*, bài [13](13_NextSteps.md)). Ta test **code
của mình**: graph route đúng, tool lọc đúng user, ingest đúng trạng thái, stream đúng giao thức, memory hoạt động.

## 2. Fake embeddings + Chroma in-memory

```python
    chroma_client = chromadb.EphemeralClient()
    vector_store = DocumentVectorStore(
        chroma_client,
        DeterministicFakeEmbedding(size=64),
        collection_name=f"test-{uuid.uuid4().hex}",  # isolated per test
    )
```

- `DeterministicFakeEmbedding` sinh vector giả từ hash của văn bản: **cùng chuỗi → cùng vector**, không gọi
  mạng. Không mang ngữ nghĩa, nên test không khẳng định "đoạn liên quan nhất đứng đầu" — chỉ khẳng định
  đoạn **có/không** xuất hiện.
- `EphemeralClient` là Chroma thật chạy in-process → metadata filter `user_id` được kiểm tra **thật**,
  không phải mock.
- Tên collection ngẫu nhiên: các `EphemeralClient` trong cùng process chia sẻ dữ liệu, nên mỗi test cần
  collection riêng để không "rò" sang nhau.

## 3. `FakeToolCallingModel`

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

`GenericFakeChatModel` nhận một **iterator** `AIMessage` và trả lần lượt mỗi lần được gọi.

1. **`bind_tools` no-op**: bản gốc raise `NotImplementedError`; fake không cần schema vì câu trả lời đã kịch bản sẵn.
2. **`disable_streaming = True`**: graph chạy với `stream_mode="messages"` → LangChain gọi model ở chế độ
   stream; cách fake tự chia chunk **làm mất `tool_calls`**. Tắt stream → trả nguyên `AIMessage`.
   Đây là lý do `ChatService` kiểm tra `isinstance(chunk, AIMessage)` thay vì `AIMessageChunk`.

Fixture `fake_llm` được dùng chung cho graph trong `app`; mỗi test gán kịch bản riêng:

```python
@pytest.fixture
def fake_llm() -> FakeToolCallingModel:
    """Tests script the replies with: fake_llm.messages = iter([AIMessage(...), ...])."""
    return FakeToolCallingModel(messages=iter([]))
```

## 4. Kịch bản: search rồi trả lời

`backend/tests/test_chat.py`:

```python
def search_then_answer(answer: str) -> list[AIMessage]:
    return [
        AIMessage(
            "Let me search.",
            tool_calls=[{"id": "c1", "name": "search_documents", "args": {"query": "Atlas"}}],
        ),
        AIMessage(answer),
    ]
```

Mỗi phần tử = **một lượt gọi LLM**. Lượt 1 trả `tool_calls` → `ToolNode` chạy **`search_documents` thật**
trên Chroma in-memory với `user_id` từ context. Lượt 2 trả câu trả lời cuối.

Helper `chat()` gọi endpoint SSE thật, kiểm tra header rồi `parse_sse` body thành `[(event, data), ...]`:

```python
async def chat(client: AsyncClient, message: str, conversation_id: str | None = None) -> Any:
    body = {"message": message, "conversation_id": conversation_id}
    async with client.stream("POST", "/api/v1/chat/stream", json=body) as response:
        assert response.status_code == 200, await response.aread()
        # Proxies must not gzip (= buffer) the stream.
        assert "no-transform" in response.headers.get_list("cache-control")
        return parse_sse((await response.aread()).decode())
```

Test chính (rút gọn):

```python
async def test_chat_searches_own_documents(
    client: AsyncClient, fake_llm: FakeToolCallingModel
) -> None:
    await register_and_login(client)
    await upload(client)
    fake_llm.messages = iter(search_then_answer("Atlas launches in March [atlas.txt]."))

    events = await chat(client, "When does Atlas launch?")

    types = [e for e, _ in events]
    assert types[0] == "metadata" and types[-1] == "done"
    tool_output = next(d["content"] for e, d in events if e == "tool_end")
    assert "[atlas.txt]" in tool_output and "billing platform" in tool_output
```

Một test này đi qua **toàn bộ** hệ thống: auth cookie → upload → S3 (moto) → ingest nền → Chroma →
graph → tool → SSE. Hết message trong iterator mà graph còn gọi LLM → `StopIteration` → test fail: tín
hiệu graph gọi LLM nhiều hơn bạn nghĩ.

## 5. Test cách ly giữa user

```python
async def test_search_never_returns_other_users_documents(
    client: AsyncClient, make_client: Any, fake_llm: FakeToolCallingModel
) -> None:
    await register_and_login(client, "alice@example.com")
    await upload(client)  # Alice's document

    async with make_client() as bob:
        await register_and_login(bob, "bob@example.com")
        fake_llm.messages = iter(search_then_answer("I found nothing."))
        events = await chat(bob, "When does Atlas launch?")

    tool_output = next(d["content"] for e, d in events if e == "tool_end")
    assert "billing platform" not in tool_output
    assert "No relevant passages" in tool_output
```

Bob đã **ép** agent search (kịch bản fake luôn gọi tool) — nhưng filter `user_id` lấy từ context của Bob
nên không thấy gì. Đây là kiểu test nên có cho **mọi** đường dữ liệu theo tenant. `make_client` tạo
client riêng (cookie jar riêng) cho user thứ hai. Tương tự, `test_cannot_use_someone_elses_conversation`
kiểm tra Bob nhận 404 khi dùng `conversation_id` của Alice.

## 6. Test graph trực tiếp (không HTTP)

Package agent độc lập với FastAPI, nên test graph thuần được:

```python
async def test_agent_searches_then_answers() -> None:
    vs = DocumentVectorStore(chromadb.EphemeralClient(), DeterministicFakeEmbedding(size=8), "t")
    model = FakeToolCallingModel(messages=iter([
        AIMessage("", tool_calls=[{"id": "c1", "name": "search_documents", "args": {"query": "x"}}]),
        AIMessage("Not in your documents."),
    ]))
    graph = build_agent_graph(model, get_tools(vs))

    result = await graph.ainvoke(
        {"messages": [HumanMessage("x?")]}, context=AgentContext(user_id=uuid.uuid4())
    )

    tool_msg = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert "No relevant passages" in tool_msg.text
```

Không truyền checkpointer → không cần `thread_id`. Test `async def` chạy được nhờ `asyncio_mode = "auto"`.

## ⚠️ Lỗi thường gặp

- **Dùng chung tên collection giữa các test** → dữ liệu test này lọt sang test khác (flaky).
- **Khẳng định thứ hạng kết quả với fake embedding** → vô nghĩa; chỉ khẳng định có/không.
- **Số message kịch bản ít hơn số lượt gọi LLM** → lỗi khó hiểu do iterator cạn.
- **`messages=[...]` (list) thay vì `iter([...])`** → `ValidationError`.
- **Quên `context=` khi test graph trực tiếp** → tool lỗi `NoneType`.
- **Để lifespan thật chạy trong test** → cần Postgres/Chroma/API key. Fixture `app` tự gán `app.state.*` và không chạy lifespan.

## 🧪 Thực hành

1. Tạo `tests/test_graph.py` với test ở mục 6. Thêm test: kịch bản không gọi tool → state chỉ có 2 message.
2. Viết test: Alice upload, rồi **xoá** tài liệu, rồi chat với kịch bản search → tool trả "No relevant passages" (kiểm chứng `delete_document` xoá chunk trong Chroma).
3. Viết test kịch bản **hai** tool call trong một `AIMessage` và kiểm tra có 2 event `tool_end`.

---

⬅️ Trước: [11 · Streaming](11_Streaming.md) · ➡️ Tiếp: [13 · Next Steps](13_NextSteps.md)
