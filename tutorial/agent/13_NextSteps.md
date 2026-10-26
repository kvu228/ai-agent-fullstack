# 13 · Next Steps: đưa trợ lý tài liệu đi xa hơn

## 🎯 Mục tiêu

- Nhìn lại kiến trúc agent RAG đã xây và biết **giới hạn** hiện tại của nó.
- Biết các hướng nâng cấp phổ biến: chất lượng retrieval, quan sát (observability), vận hành, tính năng agent.
- Với mỗi hướng: **vì sao cần**, **sửa ở đâu** trong project, và test thế nào.

## 📁 File liên quan

- `backend/app/agents/` — `graph.py`, `nodes.py`, `tools.py`, `prompts.py`, `state.py`
- `backend/app/vectorstore/chroma.py` — `DocumentVectorStore`
- `backend/app/ingestion/loaders.py`, `backend/app/ingestion/pipeline.py`
- `backend/app/services/chat_service.py`
- `backend/tests/` — mọi thay đổi nên kèm test theo kiểu bài [12](12_TestingAgents.md)

---

## 1. Nhìn lại: ta đã có gì?

```mermaid
flowchart LR
    U[Upload] --> S3[(S3)] --> I[Ingest nền<br/>load → chunk → embed] --> C[(Chroma<br/>metadata user_id)]
    Q[Câu hỏi] --> A[agent] -- tool_calls --> T["search_documents<br/>(filter user_id từ context)"]
    T --> C
    T --> A
    A --> SSE[SSE stream] 
    A <--> PG[(Postgres checkpoints)]
```

| Bài | Khái niệm |
|---|---|
| [01](01_LangChainFundamentals.md)–[02](02_Prompts.md) | Model, message, prompt có trích dẫn |
| [03](03_DocumentLoadersAndChunking.md)–[05](05_IngestionPipeline.md) | Loader, chunking, embeddings, Chroma, pipeline ingest |
| [06](06_Tools.md)–[09](09_GraphAndReActLoop.md) | Tool với `ToolRuntime`, state, node, graph ReAct, context |
| [10](10_MemoryPostgresCheckpointer.md)–[12](12_TestingAgents.md) | Memory Postgres, streaming, testing |

Phần còn lại là danh sách hướng đi — chọn theo vấn đề bạn **thực sự gặp**, đừng làm tất cả.

## 2. Viết gọn hơn với `create_agent`

LangChain v1 có `create_agent` dựng sẵn vòng ReAct (model ↔ tools) mà ta tự viết ở bài 08–09:

```python
from langchain.agents import create_agent

graph = create_agent(
    model, tools=get_tools(vector_store), system_prompt=SYSTEM_PROMPT,
    context_schema=AgentContext, checkpointer=checkpointer,
)
```

Project tự dựng `StateGraph` để bạn **thấy** từng mảnh. Nếu chuyển sang `create_agent`, lưu ý tên node
là `"model"` và `"tools"` — phải sửa `AGENT_NODE` mà `ChatService` dùng để lọc token (bài [11](11_Streaming.md)),
nếu không frontend sẽ không nhận token nào. Test hiện có sẽ bắt được lỗi này.

## 3. Quan sát & đánh giá: LangSmith

Test với fake model chứng minh code đúng, **không** chứng minh câu trả lời tốt. Cần:

- **Tracing**: xem từng lượt LLM, query agent gửi cho tool, passage trả về, token, độ trễ.
- **Evaluation**: bộ câu hỏi mẫu + đáp án, chạy định kỳ, chấm "có trích dẫn đúng nguồn không",
  "có bịa khi tài liệu không có không".

```bash
export LANGSMITH_TRACING=true
export LANGSMITH_API_KEY=...
export LANGSMITH_PROJECT=document-assistant
```

⚠️ Biến phải nằm trong **môi trường process**. `Settings` (pydantic-settings) đọc `.env` vào object
nhưng **không** export ra `os.environ`, nên chỉ thêm vào `.env` của backend là chưa đủ — đặt trong
`environment:` của docker compose / task definition.

## 4. Chất lượng retrieval

| Kỹ thuật | Vấn đề giải quyết | Sửa ở đâu |
|---|---|---|
| **Reranking** (Cohere Rerank, cross-encoder) | Top-k theo vector chưa chắc là đoạn tốt nhất | Lấy `k=20` từ Chroma, rerank còn 4 trong `DocumentVectorStore.search` (mục [4.1](#41-reranking-chi-tiết)) |
| **Hybrid search** (BM25 + vector) | Từ khoá chính xác (mã lỗi, tên riêng) mà embedding bỏ lỡ | Thêm index từ khoá, gộp điểm bằng Reciprocal Rank Fusion |
| **Query rewriting** | Câu hỏi nối tiếp "còn dự án kia thì sao?" thiếu ngữ cảnh | Agent đã tự viết query; có thể thêm node rewrite trước `agent` |
| **Ngưỡng similarity** | Đoạn "gần nhất" vẫn không liên quan | Lọc theo điểm trong tool, trả "No relevant passages" |
| **Chunking theo cấu trúc** | Bảng, heading bị cắt ngang | Splitter theo Markdown/HTML header (bài [03](03_DocumentLoadersAndChunking.md)) |

Thêm node dùng LLM (rewrite, chấm điểm) → nhớ token của nó cũng vào mode `messages`; filter
`langgraph_node` hiện tại đã chặn đúng.

### 4.1 Reranking chi tiết

#### Vì sao top-k theo vector chưa đủ tốt?

Embedding search (bài [04](04_EmbeddingsAndVectorStore.md)) dùng **bi-encoder**: câu hỏi và chunk được
mã hoá **riêng rẽ** thành vector, rồi mới so khoảng cách. Vector của chunk tính sẵn lúc ingest nên search
rất nhanh, nhưng model **không bao giờ nhìn câu hỏi và chunk cùng lúc**. Mọi ý nghĩa của một chunk bị nén
vào một vector duy nhất, nên thứ hạng chỉ ở mức "đại khái đúng chủ đề".

**Reranker** (thường là **cross-encoder**) nhận **cặp `(câu hỏi, chunk)`** làm một input và trả về một
điểm liên quan. Nó đọc cả hai cùng lúc, nên phân biệt được "chunk nói *về* Atlas" với "chunk *trả lời*
khi nào Atlas ra mắt".

| | Bi-encoder (embedding) | Cross-encoder (reranker) |
|---|---|---|
| Input | Từng văn bản riêng | Cặp (câu hỏi, chunk) |
| Tính trước được? | ✅ Vector chunk lưu sẵn trong Chroma | ❌ Phải chạy lại cho mỗi câu hỏi |
| Tốc độ | Rất nhanh, search hàng triệu chunk | Chậm, chỉ chấm được vài chục chunk |
| Độ chính xác thứ hạng | Khá | Tốt hơn rõ rệt |

Vì vậy hai loại được ghép thành **tìm kiếm hai tầng**: tầng rẻ lấy nhiều ứng viên (recall cao), tầng
đắt chọn ra vài đoạn tốt nhất (precision cao).

```mermaid
flowchart LR
    Q[query] --> C["Chroma<br/>filter user_id<br/>k = 20"]
    C -->|20 ứng viên| R["Reranker<br/>chấm từng cặp<br/>(query, chunk)"]
    R -->|top 5| T[search_documents<br/>→ LLM]
```

#### Các lựa chọn reranker

| Cách | Ví dụ | Ưu | Nhược |
|---|---|---|---|
| **Rerank API** | Cohere Rerank, Voyage, Jina | Tích hợp vài dòng, chất lượng tốt, đa ngôn ngữ | Tốn phí theo request, phụ thuộc mạng, gửi nội dung tài liệu ra ngoài |
| **Cross-encoder local** | `BAAI/bge-reranker-*`, `cross-encoder/ms-marco-*` (sentence-transformers) | Miễn phí, dữ liệu không rời server | Cần `torch` → image Docker nặng; chậm nếu không có GPU |
| **LLM làm reranker** | Prompt LLM chấm điểm / xếp hạng các passage | Linh hoạt, hiểu yêu cầu phức tạp | Chậm và tốn token nhất |

LangChain có sẵn `ContextualCompressionRetriever` cùng các compressor như `CohereRerank`. Project này
không dùng retriever của LangChain mà đi qua `DocumentVectorStore`, nên gọi reranker ngay trong `search`
là gọn nhất.

#### Gắn vào project

Giữ tinh thần của `DocumentVectorStore`: tool và agent **không cần biết** có rerank hay không. Chỉ
`search` thay đổi.

```python
class Reranker(Protocol):
    async def rerank(
        self, query: str, docs: list[LCDocument], top_n: int
    ) -> list[tuple[LCDocument, float]]: ...


class CrossEncoderReranker:
    def __init__(self, model_name: str) -> None:
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(model_name)

    async def rerank(
        self, query: str, docs: list[LCDocument], top_n: int
    ) -> list[tuple[LCDocument, float]]:
        pairs = [(query, doc.page_content) for doc in docs]
        # Model inference is CPU-bound: keep it off the event loop.
        scores = await asyncio.to_thread(self._model.predict, pairs)
        ranked = sorted(zip(docs, scores, strict=True), key=lambda pair: pair[1], reverse=True)
        return [(doc, float(score)) for doc, score in ranked[:top_n]]
```

```python
class DocumentVectorStore:
    def __init__(self, ..., reranker: Reranker | None = None, candidate_k: int = 20) -> None:
        ...
        self._reranker = reranker
        self._candidate_k = candidate_k

    async def search(
        self, query: str, user_id: uuid.UUID, k: int
    ) -> list[tuple[LCDocument, float]]:
        # With a reranker, fetch more candidates than needed and let it pick the best k.
        fetch_k = max(k, self._candidate_k) if self._reranker else k
        results = await self._store.asimilarity_search_with_score(
            query, k=fetch_k, filter={"user_id": str(user_id)}
        )
        if self._reranker is None:
            return [(doc, 1.0 - distance) for doc, distance in results]
        # Candidates are already filtered by user_id: the reranker never sees other users' chunks.
        return await self._reranker.rerank(query, [doc for doc, _ in results], top_n=k)
```

Những chỗ khác cần sửa theo:

- **`config.py`**: thêm `rerank_model: str | None = None` và `rerank_candidate_k: int = 20`. Để `None`
  thì tắt rerank, giữ hành vi cũ.
- **Lifespan (`main.py`)**: tạo reranker **một lần** (load model mất vài giây và tốn RAM), truyền vào
  `DocumentVectorStore`.
- **`tests/conftest.py`**: lifespan không chạy trong test, nên phải truyền reranker giả ở đây. Ví dụ
  `FakeReranker` đảo ngược thứ tự ứng viên: test kiểm tra kết quả theo đúng thứ tự reranker trả về và có
  đúng `k` phần tử, không cần tải model thật.
- **`tools.py`**: nhãn `(similarity 0.82)` không còn đúng, vì điểm giờ là **điểm của reranker**, thang
  đo khác (tuỳ model: 0–1 hoặc logit không giới hạn). Đổi thành `(relevance …)`, và nếu có ngưỡng lọc
  thì phải chọn lại ngưỡng.

Với Cohere thì không cần `torch`: `CohereRerank(model=..., top_n=k)` từ `langchain-cohere`, gọi
`await reranker.acompress_documents(docs, query)`, điểm nằm ở `doc.metadata["relevance_score"]`. Bọc nó
trong một class có cùng method `rerank` như trên là thay được cho nhau.

#### Chọn `candidate_k`

| `candidate_k` | Hệ quả |
|---|---|
| Quá nhỏ (≈ `k`) | Reranker chỉ sắp xếp lại đúng những gì vector search đã chọn, gần như vô ích |
| 20–50 | Mức thường dùng: đủ rộng để "cứu" đoạn đúng bị xếp hạng thấp, vẫn đủ nhanh |
| Hàng trăm | Độ trễ tăng tuyến tính, và agent ReAct có thể gọi tool **nhiều lần** mỗi câu hỏi |

Đo trước khi chọn: với bộ câu hỏi eval (mục 3), so **recall@k** (đoạn đúng có nằm trong top-k không)
khi tắt và bật rerank, cùng độ trễ của tool trong LangSmith.

## 5. Ingestion cho production

- **Task queue thật** (Celery, arq, SQS + worker): `BackgroundTasks` chạy trong process API — restart là
  mất việc đang dở, file lớn chiếm CPU của API. Queue cho retry, giới hạn đồng thời, scale worker riêng.
- **OCR cho PDF scan**: `pypdf` (`_load_pdf`) chỉ đọc text layer; PDF ảnh cho 0 chunk. Dùng Textract / Unstructured
  / Tesseract, hoặc ít nhất đánh dấu `failed` với thông báo rõ.
- **Định dạng mới** (DOCX, HTML): thêm hàm vào `LOADERS` + `EXTENSION_TO_CONTENT_TYPE` trong `loaders.py`, kèm test như `test_text_loader`.
- **Re-ingest khi đổi model embedding**: vector của model cũ và mới không so được với nhau — cần collection
  mới và chạy lại toàn bộ.

## 6. Trải nghiệm & an toàn

- **Event `sources` khi stream**: gửi danh sách file đã trích dẫn để frontend hiện chip nguồn, click mở
  presigned URL (bài [11](11_Streaming.md), thực hành 3).
- **Rate limit theo user**: mỗi câu hỏi tốn tiền LLM + embedding. Giới hạn số request/phút theo `user_id`
  (Redis token bucket), và hạn mức upload.
- **Giới hạn ngữ cảnh**: hội thoại dài + nhiều `ToolMessage` → vượt context window, chi phí tăng.
  Dùng `trim_messages` trong `AgentNode` hoặc `SummarizationMiddleware` (nếu dùng `create_agent`). Chỉ cắt
  phần gửi LLM, checkpoint vẫn giữ đầy đủ.
- **Prompt injection từ tài liệu**: passage là dữ liệu do user tải lên, có thể chứa "bỏ qua hướng dẫn…".
  Bọc passage trong delimiter rõ ràng và nhắc trong system prompt rằng đó là dữ liệu, không phải lệnh.

## 7. Tính năng agent nâng cao

- **Structured output**: `model.with_structured_output(Answer)` với `Answer(text, citations: list[Citation])`
  → frontend render trích dẫn chắc chắn thay vì parse `[file, p.N]`.
- **Human-in-the-loop**: `interrupt()` của LangGraph dừng graph chờ user xác nhận (vd. trước khi tool
  xoá/gửi gì đó); resume bằng `Command(resume=...)`. Cần checkpointer — ta đã có.
- **Multi-agent**: agent "nghiên cứu" search sâu, agent "viết" tổng hợp. Chỉ nên làm khi một agent với
  vài tool không còn đủ.
- **Long-term memory**: `BaseStore` của LangGraph lưu sở thích của user **xuyên hội thoại** (namespace
  theo `user_id`), khác checkpointer chỉ nhớ trong một thread.

## ⚠️ Lỗi thường gặp

- **Tối ưu trước khi đo**: thêm reranker/hybrid khi chưa có bộ eval → không biết tốt lên hay xấu đi.
- **Đổi tên node khi chuyển sang `create_agent`** mà quên `AGENT_NODE` → stream không có token.
- **Bật LangSmith ở production mà không nghĩ tới dữ liệu**: trace chứa nội dung tài liệu của user. Kiểm
  tra chính sách dữ liệu, hoặc ẩn input/output (`LANGSMITH_HIDE_INPUTS`).
- **Bỏ filter `user_id` khi thêm đường search mới** (BM25, reranker…) → rò dữ liệu giữa user. Copy test
  `test_search_never_returns_other_users_documents` cho mọi đường mới.
- **Load model reranker trong mỗi request** thay vì một lần trong lifespan → mỗi lần search chậm vài giây.
- **Gọi `CrossEncoder.predict` trực tiếp trong code async** → chặn event loop, mọi stream khác bị đứng.
  Bọc bằng `asyncio.to_thread`.
- **So điểm reranker với ngưỡng cosine cũ** → thang đo khác nhau, ngưỡng mất ý nghĩa.

## 🧪 Thực hành

1. Tạo 10 câu hỏi + đáp án từ một tài liệu của bạn, chạy qua `scripts/run_agent.py` với LangSmith bật, ghi lại câu sai.
2. Thêm ngưỡng similarity vào tool, viết test với fake embedding (truy vấn đúng nguyên văn chunk → similarity cao nhất).
3. Thêm `trim_messages` vào `AgentNode` (giữ ~4000 token cuối) và test rằng checkpoint vẫn đủ message.
4. Thêm `Reranker` vào `DocumentVectorStore` theo mục 4.1, với `FakeReranker` trong `conftest.py`. Chạy
   lại `test_search_never_returns_other_users_documents` để chắc rerank không làm rò dữ liệu, rồi so
   recall@k trên bộ câu hỏi ở bài tập 1 khi tắt / bật rerank.

---

⬅️ Trước: [12 · Testing Agents](12_TestingAgents.md) · ➡️ Tiếp: [Backend 13 · Streaming SSE](../backend/13_StreamingSSE.md)
