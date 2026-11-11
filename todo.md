# TODO

Lộ trình nâng cấp project để luyện kỹ năng AI engineer / SWE ở level middle → early senior.

Mỗi mục được coi là xong khi để lại đủ 3 thứ:

- **Quyết định có lý do**: một ADR ngắn (`docs/adr/NNNN-*.md`) ghi bối cảnh, các phương án và trade-off.
- **Con số đo được**: trước/sau, ví dụ recall@k, p95 latency, số job mất khi restart.
- **Test**: chạy trong `make test` hoặc trong CI.

Mỗi mục cũng có một bài tutorial (tiếng Việt, format `xx_Name.md` với các phần 🎯 / 📁 / ⚠️ / 🧪) trong `tutorial/platform/`, và cập nhật `CLAUDE.md` + `tutorial/00_Overview.md` khi thêm thư mục, lệnh hoặc service mới.

---

## 1. CI với GitHub Actions

Nền móng: các mục sau đều chạy trong CI.

- [x] `.github/workflows/ci.yml` chạy cho mỗi PR và mỗi push lên `main`
  - [x] Job backend: setup `uv` (có cache), `uv sync`, `ruff check`, `ruff format --check`, `mypy`, `pytest`
  - [x] Job frontend: setup Node (cache npm), `npm ci`, `lint`, `typecheck`, `vitest`
  - [x] Chạy hai job song song; dùng `paths` filter để PR chỉ sửa frontend thì không chạy backend (job `changes` + `dorny/paths-filter`, không dùng `on.paths`)
- [x] Job build Docker image cho `backend/` và `frontend/` (chỉ build, chưa push) để bắt lỗi Dockerfile sớm
- [x] Scan image bằng Trivy (đã nằm trong checklist ở backend/16). Lần đầu tìm ra `certifi`/`filelock`/`pypdf` và `npm` trong image frontend, đã sửa
- [x] Coverage cho pytest (`pytest-cov`), in báo cáo trong summary của job. Baseline 85%, `fail_under = 80`
- [x] Bật branch protection cho `main`: bắt buộc check `CI result` trước khi merge (cần workflow chạy ít nhất một lần trên GitHub; các bước ở platform/01 mục 8)
- [x] Dependabot cho `uv.lock`, `package-lock.json`, GitHub Actions và base image
- [x] ADR: `docs/adr/0001-ci-github-actions.md`
- [x] Tutorial: `tutorial/platform/01_ContinuousIntegration.md`
- [ ] Ghi thời gian CI lần đầu và lần có cache vào bảng ở platform/01 mục 10

**Xong khi**: PR mới tự chạy lint + test + build; tổng thời gian CI được ghi lại, có thể giảm nhờ cache.

## 2. Evaluation cho RAG agent

Có số đo trước, rồi mới tối ưu (rerank, hybrid search, chunking...). Xem `tutorial/agent/13_NextSteps.md` §3, §4.

- [ ] Tạo bộ tài liệu mẫu cố định trong `backend/evals/fixtures/` (vài PDF/MD, không có dữ liệu nhạy cảm)
- [ ] Golden dataset `backend/evals/dataset.jsonl`: khoảng 30–50 câu hỏi, mỗi câu có
  - đáp án mong đợi
  - chunk/trang nguồn đúng (để tính retrieval metrics)
  - nhãn loại câu: trả lời được / **không có trong tài liệu** (agent phải nói không biết) / câu nối tiếp nhiều lượt
- [ ] Đánh giá retrieval, không cần LLM: recall@k, MRR của `DocumentVectorStore.search`
- [ ] Đánh giá câu trả lời: citation accuracy (trích đúng file/trang), faithfulness (không bịa), từ chối đúng khi tài liệu không có
  - [ ] Thử LLM-as-judge; ghi lại các hạn chế của nó (bias, chi phí, độ ổn định giữa các lần chạy)
- [ ] Chọn công cụ và ghi ADR: LangSmith evals, RAGAS hoặc promptfoo
- [ ] Script `uv run python -m evals.run` (cần infra + API key) xuất bảng kết quả, lưu kết quả vào file để so sánh giữa các lần
- [ ] Đo chi phí (token) và độ trễ của mỗi lần chạy eval
- [ ] Thêm eval vào CI: chỉ chạy khi PR sửa `app/agents/`, `app/vectorstore/`, `app/ingestion/` hoặc prompt; fail nếu metric giảm quá ngưỡng
- [ ] Bộ câu hỏi prompt injection (passage chứa "bỏ qua hướng dẫn...") để kiểm tra agent không làm theo
- [ ] Áp dụng: dùng bộ eval để quyết định có bật reranking (agent/13 §4.1) hay không, ghi kết quả trước/sau
- [ ] Tutorial: `tutorial/platform/02_Evaluation.md`

**Xong khi**: có bảng baseline (recall@k, citation accuracy, faithfulness, chi phí, latency) và một thay đổi retrieval được quyết định dựa trên số liệu đó.

## 3. Hàng đợi + Redis

Thay `BackgroundTasks`: hiện ingestion chạy trong process API, mất job khi container restart, document kẹt ở `processing`.

### 3a. Hàng đợi cho ingestion

- [ ] ADR: SQS + worker, arq (Redis) hay Celery; vì sao chưa cần Kafka
- [ ] Môi trường local: kiểm tra Floci có giả lập SQS không, nếu không thì dùng ElasticMQ trong `docker-compose.yml`
- [ ] `POST /documents` gửi message `{document_id}` vào queue thay vì `BackgroundTasks`
- [ ] Worker riêng (`backend/app/worker.py`, service mới trong compose, target `make dev-worker`)
- [ ] **Idempotency**: chạy lại cùng một message không tạo chunk trùng (đã có id deterministic `"{document_id}:{i}"`; kiểm tra cả trường hợp document đã `ready`)
- [ ] Retry với backoff, giới hạn số lần, dead-letter queue; document vào DLQ thì chuyển `failed` kèm lý do
- [ ] Visibility timeout lớn hơn thời gian ingest file lớn nhất (`MAX_UPLOAD_MB`)
- [ ] Giới hạn concurrency của worker (tránh vượt rate limit của embedding API)
- [ ] Job dọn document kẹt ở `processing` quá lâu
- [ ] Test: dùng moto cho SQS (hoặc fake queue) trong `conftest.py`; nhớ thêm resource mới vào fixture `app` vì lifespan không chạy trong test
- [ ] Đo: kill worker giữa chừng, xác nhận job được xử lý lại và không mất document nào

### 3b. Redis

- [ ] Thêm Redis vào `docker-compose.yml` và `Settings`
- [ ] Rate limit theo `user_id` (token bucket) cho `/chat/stream` và upload; trả 429 với `RateLimitedError` (bài tập ở backend/14)
- [ ] Rate limit theo IP cho `/auth/login`, `/auth/register` (checklist backend/16)
- [ ] Cân nhắc cache: kết quả embedding của query, hoặc semantic cache cho câu hỏi lặp lại. Ghi ADR về invalidation (khi user xoá/upload tài liệu) và về rủi ro rò dữ liệu giữa các user (cache key phải có `user_id`)
- [ ] Test: fake Redis (`fakeredis`) trong `conftest.py`

- [ ] Tutorial: `tutorial/platform/03_TaskQueue.md`, `tutorial/platform/04_Redis.md`

**Xong khi**: restart API hoặc worker giữa lúc ingest không làm mất document; user vượt hạn mức nhận 429.

## 4. Observability + load test

Checklist ở backend/16: log JSON có request id, metrics, tracing, cảnh báo.

### 4a. Logging, metrics, tracing

- [ ] Log JSON có cấu trúc (thay `basicConfig` trong `app/core/logging.py`), kèm `request_id`, `user_id`, `conversation_id`
- [ ] Middleware gán `request_id` (nhận từ header `X-Request-ID` nếu có), trả lại trong response, truyền sang worker qua message
- [ ] OpenTelemetry: trace xuyên qua FastAPI → SQLAlchemy → S3 → Chroma → LLM call → worker
- [ ] Metrics (Prometheus):
  - [ ] HTTP: request rate, tỉ lệ lỗi 5xx, latency p50/p95/p99 theo endpoint
  - [ ] Chat: time-to-first-token, tổng thời gian stream, số stream đang mở
  - [ ] LLM: token in/out và chi phí ước tính theo model, theo user
  - [ ] Ingestion: độ dài queue, thời gian xử lý, số document `failed`
- [ ] Grafana (thêm vào compose) với một dashboard lưu dưới dạng file trong repo
- [ ] Định nghĩa 2–3 SLO (ví dụ: 99% request `/chat/stream` có time-to-first-token < 3 s) và alert tương ứng
- [ ] Kết nối với LangSmith (đã có trong `config.py`): trace của LLM liên kết được với `request_id`

### 4b. Load test

- [ ] ADR: k6 hay Locust
- [ ] Kịch bản: đăng nhập → upload → chờ `ready` → chat nhiều lượt (SSE). Dùng fake LLM hoặc model rẻ để không tốn tiền
- [ ] Tìm giới hạn: bao nhiêu stream SSE đồng thời trước khi p95 vượt SLO? Nghẽn ở đâu (DB pool, event loop, Chroma, LLM rate limit)?
- [ ] Sửa một điểm nghẽn, đo lại và ghi kết quả trước/sau
- [ ] Kiểm tra công thức kết nối DB ở backend/16: `replicas × workers × (pool_size + max_overflow + pool checkpointer) < max_connections`

- [ ] Tutorial: `tutorial/platform/05_Observability.md`, `tutorial/platform/06_LoadTesting.md`

**Xong khi**: có dashboard và SLO; có một báo cáo load test ghi rõ giới hạn hiện tại, điểm nghẽn và kết quả sau khi sửa.

## 5. Human-in-the-loop: xoá tài liệu qua chat có xác nhận

Làm sau khi mục 2 có baseline end-to-end (mốc 2), để eval chấm được cả tính năng này. Xoá là hành động không hoàn tác được, nên cần người duyệt là có lý do thật.

- [ ] ADR: `interrupt()` bên trong tool, `interrupt_before` + router safe/sensitive, hay `HumanInTheLoopMiddleware` của `create_agent`; vì sao interrupt tĩnh (`interrupt_before`) chỉ nên dùng để debug
- [ ] Tool `delete_document`: gọi `interrupt({...})` ở **đầu** tool, trước mọi tác dụng phụ (khi resume, tool chạy lại từ đầu); `user_id` vẫn lấy từ `runtime.context`
- [ ] Dùng lại `DocumentService.delete` (row + chunk Chroma + file S3), không viết lại logic xoá; kiểm tra quyền sở hữu lúc thực thi
- [ ] `ChatService.stream`: nhận `__interrupt__` trong mode `updates`, phát `StreamEvent` mới `approval_required` (tên file, thông tin cần duyệt) thay cho `done`
- [ ] Endpoint resume (`POST /chat/resume`): `astream(Command(resume=decision), config)` trên cùng `thread_id`; kiểm tra quyền sở hữu hội thoại trong dependency, như `get_chat_conversation`
- [ ] Quyết định: đồng ý / huỷ (cân nhắc có cho sửa tham số không)
- [ ] Chính sách khi user không trả lời mà gửi tin nhắn mới: tự coi là huỷ
- [ ] Một lượt có nhiều tool call → nhiều interrupt cùng lúc
- [ ] `messages()` đọc interrupt đang chờ từ `aget_state`, để tải lại trang vẫn thấy yêu cầu duyệt
- [ ] Frontend: event mới trong `sse.ts` / `chatReducer`, nút Đồng ý / Huỷ, test cho reducer
- [ ] Test backend (fake LLM + `InMemorySaver`): nhánh đồng ý, nhánh huỷ, resume hội thoại của user khác bị 404
- [ ] Eval: thêm câu vào golden dataset: không bao giờ xoá khi chưa được duyệt; huỷ thì tài liệu còn nguyên; câu không liên quan không gọi tool xoá
- [ ] Đo: restart API trong lúc đang chờ duyệt, resume vẫn chạy (checkpoint nằm trong Postgres)
- [ ] Tutorial: `tutorial/platform/07_HumanInTheLoop.md`

**Xong khi**: xoá tài liệu qua chat luôn cần xác nhận (eval: 0 lần xoá khi chưa duyệt), huỷ thì không mất gì, và restart server giữa lúc chờ duyệt vẫn resume được.
