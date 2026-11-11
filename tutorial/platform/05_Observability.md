# 05 · Observability (phần 1): Logging

## 🎯 Mục tiêu

- Dùng **level** đúng nghĩa: mỗi level là một lời hứa với người đọc log lúc có sự cố.
- Viết log **có cấu trúc**: message cố định, dữ liệu trong field, JSON ở production.
- Gắn `request_id`, `user_id`, `conversation_id` vào **mọi** dòng log mà không phải truyền tham số qua từng hàm (`ContextVar`).
- Biết **không được** log những gì (secret, PII, nội dung tài liệu, prompt), và có lưới an toàn cùng test để chứng minh.
- Log **một lần**, ở chỗ xử lý lỗi.
- Hiểu những chỗ dễ sai khi kết hợp với uvicorn, FastAPI CLI, middleware và SSE.

Phần 2 (log một lượt chạy agent, audit log, chi phí và sampling, OpenTelemetry, metrics) nằm ở `todo.md` §4a.

## 📁 File liên quan

- `backend/app/core/logging.py`: context, formatter JSON/text, redaction, `setup_logging`
- `backend/app/core/middleware.py`: `RequestContextMiddleware`
- `backend/app/core/exceptions.py`: handler 500 gắn `X-Request-ID`
- `backend/app/api/deps.py`: bind `user_id`, `conversation_id`
- `backend/app/services/*.py`, `backend/app/ingestion/pipeline.py`: các dòng log có cấu trúc
- `backend/tests/test_logging.py`
- `docs/adr/0003-structured-logging.md`

---

## 1. Log để làm gì, và ai đọc nó?

Người đọc log là **bạn lúc 2 giờ sáng**, khi một user báo "chat bị lỗi". Câu hỏi họ cần trả lời là: *chuyện gì đã xảy ra với request đó?* Mọi quyết định trong bài này đều nhằm làm câu hỏi đó dễ trả lời.

Log chỉ là một trong ba công cụ:

| Công cụ | Trả lời câu hỏi | Ví dụ |
| --- | --- | --- |
| **Log** | Chuyện gì đã xảy ra với request / document **này**? | "Login failed, reason=wrong_password, request_id=…" |
| **Metric** | Hệ thống có đang khoẻ không? Có cần báo động không? | Tỉ lệ 5xx, p95 latency, số lần đăng nhập thất bại mỗi phút |
| **Trace** | Thời gian bị tiêu ở **đâu** trong một request? | FastAPI → Postgres → Chroma → LLM, mỗi bước bao nhiêu ms |

Sai lầm hay gặp là dựng metric bằng cách đếm dòng log: vừa đắt vừa chậm. Bài này chỉ nói về log.

## 2. Level là một hợp đồng

| Level | Nghĩa | Ví dụ trong project |
| --- | --- | --- |
| `DEBUG` | Chi tiết để debug, tắt ở production | Request tới `/health` (gọi mỗi 10 giây) |
| `INFO` | Sự kiện bình thường, đáng ghi lại | `Request finished`, `Login succeeded`, `Login failed`, `Document ingested` |
| `WARNING` | Bất thường, hệ thống đã tự xử lý, nhưng ai đó nên xem lại | `Refresh token reuse detected` (token có thể đã bị đánh cắp; ta đã thu hồi mọi phiên) |
| `ERROR` | Có lỗi cần **người** xử lý | `Unhandled error`, `Agent stream failed`, `Document ingestion failed` |

Hai quyết định đáng chú ý:

- **4xx không được log ở `ERROR`** (`exceptions.py`, `handle_app_error` không log gì). Sai mật khẩu hay 404 là lỗi của client và là chuyện bình thường. Nếu log ở `ERROR`, cảnh báo sẽ gọi người trực dậy vì những chuyện không ai sửa được. Status của mọi request đã có sẵn trong dòng `Request finished`.
- **Đăng nhập thất bại là `INFO`, không phải `WARNING`.** Một lần sai mật khẩu là bình thường. Brute force thể hiện ra ở **tần suất**, và việc theo dõi tần suất là của metric + alarm, không phải của từng dòng log.

## 3. Log có cấu trúc

### Message cố định, dữ liệu trong field

```python
# ❌ Mỗi document một message khác nhau: không group, không lọc theo số được
logger.info(f"Ingested document {document_id} into {n} chunks in {ms} ms")

# ✅ Message là hằng số, dữ liệu nằm trong field
logger.info("Document ingested", extra={"chunks": n, "duration_ms": ms})
```

Với message cố định, bạn đếm được "bao nhiêu lần `Document ingested` trong 1 giờ". Với field kiểu số, bạn query được "các lần ingest lâu hơn 5 giây". Tên field nên ổn định như tên field của một API: dashboard và alarm phụ thuộc vào chúng.

Nếu vẫn muốn chèn biến vào message, hãy dùng `%s` (`logger.info("Found %s", n)`) chứ đừng dùng f-string: chuỗi chỉ được ghép khi dòng log thật sự được ghi ra.

### JSON ở production, text cho người đọc

`LOG_FORMAT=json` (mặc định là `text`). Đây là output thật của server khi chạy thử:

```json
{"timestamp": "2026-11-11T16:38:45.390+00:00", "level": "INFO", "logger": "app.services.auth_service", "message": "Login failed", "env": "development", "request_id": "3fe999fe193b4c2e8529688d4551cf31", "reason": "unknown_email", "user_id": null}
{"timestamp": "2026-11-11T16:38:45.392+00:00", "level": "INFO", "logger": "app.request", "message": "Request finished", "env": "development", "request_id": "3fe999fe193b4c2e8529688d4551cf31", "http_method": "POST", "http_path": "/api/v1/auth/login", "http_status": 401, "ttfb_ms": 124.0, "total_ms": 124.1, "client_ip": "127.0.0.1"}
```

Ở dev, cùng hai dòng đó có dạng `time | LEVEL | logger | message | key=value ...`.

Một chi tiết quan trọng: **traceback nằm gọn trong một field `exception`**, trên một dòng. Nếu ghi traceback dạng text nhiều dòng, CloudWatch sẽ cắt mỗi dòng thành một event riêng, và bạn không còn đọc được nó.

## 4. Correlation: `request_id` trên mọi dòng log

### 4.1 `ContextVar` thay cho việc truyền tham số

Muốn mọi dòng log có `request_id`, cách ngây thơ là truyền `request_id` qua mọi hàm. Thay vào đó, ta lưu nó trong một `ContextVar`:

```python
_log_context: ContextVar[Mapping[str, Any]] = ContextVar("log_context", default=_EMPTY)

def bind_log_context(**fields: Any) -> None:
    """Add fields to every record logged from now on in the current request/task."""
    _log_context.set(MappingProxyType({**_log_context.get(), **fields}))
```

`ContextVar` đi theo **asyncio task**. Mỗi request chạy trong task riêng, nên giá trị gán trong request A không bao giờ lọt sang request B, kể cả khi hai request chạy xen kẽ trên cùng một event loop. Biến toàn cục hay `threading.local` không làm được điều này.

Mỗi lần bind, ta tạo một mapping **mới** chứ không sửa mapping cũ. Task con chỉ giữ bản copy của context, nên nếu sửa mapping dùng chung, task con có thể làm hỏng context của task cha.

### 4.2 Đưa context vào record: record factory, không dùng `Filter`

```python
def _record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
    record = _base_record_factory(*args, **kwargs)
    record.context = _log_context.get()
    return record
```

Hai lựa chọn có chủ ý:

- **Record factory thay vì `logging.Filter`.** Một filter gắn vào handler chỉ chạy cho handler đó, nên `caplog` của pytest hay một handler khác thêm vào sau sẽ không thấy context. Filter gắn vào **logger** còn tệ hơn: nó không chạy cho record được propagate lên từ logger con (`app.services.chat_service` → root). Record factory thì chạy đúng một lần, ngay lúc record được tạo, trong task của người gọi log.
- **Một attribute `context` duy nhất,** thay vì gán từng field lên record. Nếu context đã gán `record.request_id`, thì `logger.info(..., extra={"request_id": ...})` sẽ ném `KeyError: Attempt to overwrite 'request_id' in LogRecord`. Formatter gộp `context` với `extra` lại, và field trong `extra` thắng khi trùng tên.

### 4.3 Middleware ASGI thuần

```python
incoming = Headers(scope=scope).get(REQUEST_ID_HEADER, "")
request_id = incoming if _VALID_REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
new_log_context(request_id=request_id)
```

- Dùng lại id do load balancer, frontend hay service khác gửi lên, nhưng **chỉ khi nó trông giống một id** (`[A-Za-z0-9._-]{1,64}`). Đó là chuỗi do client kiểm soát và đi thẳng vào log của ta: chặn log injection và id dài vô hạn.
- Bọc `send` để đọc status code và gắn `X-Request-ID` vào response.
- Đo hai mốc thời gian:
  - `ttfb_ms`: lúc header được gửi đi.
  - `total_ms`: lúc cả ASGI call kết thúc. Với `/chat/stream`, đây là toàn bộ câu trả lời của agent. Với `POST /documents`, nó gồm cả ingestion, vì BackgroundTasks chạy **sau** khi đã trả 202 nhưng vẫn trong cùng ASGI call.
- Request tới `/health` và `/health/ready` được log ở `DEBUG`: chúng chiếm phần lớn số request mà không ai cần đọc.
- Middleware này viết dạng **ASGI thuần**, không dùng `BaseHTTPMiddleware`. `BaseHTTPMiddleware` chạy app trong một task khác, nên `user_id` mà dependency bind sẽ không quay ngược ra được dòng `Request finished`. Nó cũng có tiền sử gây lỗi với streaming response.
- Route template (`/documents/{document_id}`) sẽ tốt hơn `http_path` khi cần gom nhóm, nhưng FastAPI 0.143 không có API công khai để lấy template đầy đủ (tính cả prefix `/api/v1`). OpenTelemetry sẽ cung cấp `http.route` ở phần 2.

### 4.4 Context đi tới đâu?

| Nơi chạy code | Thấy context của request? | Bind ở đó có quay ngược ra request không? |
| --- | --- | --- |
| Dependency `async def` (`get_current_user`) | ✅ | ✅ cùng task. Nhờ vậy `user_id` có trên dòng `Request finished` |
| Dependency `def` (sync, chạy trong threadpool) | ✅ (bản copy) | ❌ |
| Generator của SSE (`ChatService.stream`) | ✅ | Không cần |
| `BackgroundTasks` (ingestion) | ✅ cùng request: log ingestion mang `request_id` của **lần upload** | ✅ |
| `asyncio.to_thread(...)` | ✅ (có copy context) | ❌ |
| `loop.run_in_executor(...)` | ❌ **không** copy context | ❌ |
| Worker riêng đọc từ queue (todo §3a) | ❌ phải gửi `request_id` kèm message | |

`test_context_follows_the_upload_into_background_ingestion` chứng minh dòng thứ tư: một id theo document từ lúc upload tới lúc `ready`.

### 4.5 Vì sao middleware **không** reset context

Thông thường ta sẽ `reset(token)` khi request xong. Ở đây ta cố ý không làm vậy. Starlette đặt `ServerErrorMiddleware`, nơi chạy handler cho `Exception` (lỗi 500), **bên ngoài** mọi middleware ta thêm vào:

```
ServerErrorMiddleware (handler 500)  ← chạy SAU KHI middleware của ta đã return
  └─ RequestContextMiddleware
       └─ CORSMiddleware → ExceptionMiddleware (handler AppError) → router
```

Nếu reset, dòng log `Unhandled error` sẽ mất `request_id`, đúng ở chỗ cần nó nhất. Vì cùng lý do đó, response 500 không đi qua `send_wrapper`, nên `handle_unexpected` phải tự gắn `X-Request-ID`. Không reset vẫn an toàn: dưới uvicorn mỗi request là một task mới, nên context không lọt sang request sau.

## 5. Không log những gì

| Không log | Vì sao | Thay bằng |
| --- | --- | --- |
| Mật khẩu, access/refresh token, cookie, header `Authorization`, API key | Ai đọc được log là chiếm được tài khoản | Không gì cả |
| Email, tên file | PII. Tên file hay chứa tên người ("CV_Nguyen_Van_A.pdf") | `user_id`, `document_id`, `content_type`, `size_bytes` |
| Email khi đăng nhập **thất bại** | Người dùng hay gõ nhầm mật khẩu vào ô email | `reason` + `user_id` nếu tìm thấy user |
| Nội dung tài liệu, prompt, câu trả lời của LLM | Đó là dữ liệu của user | Trace trên LangSmith, nơi có phân quyền truy cập (phần 2) |
| Query string | Hay mang token (link reset mật khẩu…) | `http_path` không có query |

**Lưới an toàn** (`redact` trong `logging.py`):

- Field có tên giống secret (`password`, `access_token`, `api_key`, `cookie`…) được thay bằng `[REDACTED]`. Riêng `input_tokens` được giữ lại, vì đó là số đếm.
- JWT (`eyJ….….…`) và chuỗi sau `Bearer ` trong message hay traceback cũng bị che.

Đây là lưới an toàn, **không phải giấy phép** để log bừa. Regex không bắt được mọi thứ. Quy tắc chính vẫn là không đưa secret vào log ngay từ đầu, và có test để giữ quy tắc đó:

```python
async def test_tokens_never_reach_the_logs(client, caplog):
    caplog.set_level(logging.DEBUG)
    await register_and_login(client)
    access_token = client.cookies["access_token"]
    ...
    output = all_output(caplog)   # every record, rendered as production JSON
    assert access_token not in output
```

## 6. Log một lần, ở chỗ xử lý lỗi

```python
# ❌ log-and-reraise: một lỗi thành 3 dòng ERROR ở 3 tầng
try:
    await self._vector_store.add_chunks(...)
except Exception:
    logger.exception("add_chunks failed")
    raise
```

Quy tắc: **nơi nào bắt lỗi và quyết định xử lý thì nơi đó log**, kèm stack trace.

- `pipeline.py`: bắt lỗi rồi đặt `status=failed`, nên log `Document ingestion failed` ở đó.
- `chat_service.py`: 200 đã được gửi đi, nên báo lỗi bằng event `error` trong stream và log `Agent stream failed` ở đó.
- Lỗi không ai bắt: chỉ `handle_unexpected` log.

Nhờ context, các dòng này tự có `conversation_id`, `user_id`, `request_id` mà không cần thêm gì vào lời gọi log.

## 7. Uvicorn và FastAPI CLI

Uvicorn có handler và format riêng, cộng thêm access log riêng. Nếu để nguyên, mỗi request sẽ có hai dòng log với hai format khác nhau. `setup_logging` làm ba việc:

- xoá handler của logger `uvicorn` và cho log của nó đi qua handler gốc (formatter của ta),
- tắt `uvicorn.access`, vì `Request finished` đã thay thế nó và còn có context,
- chỉ thay handler mang tên `app`, giữ nguyên các handler khác (ví dụ `caplog` của pytest).

Có một bẫy về **thứ tự**: `fastapi run` *import* `app.main` (để tìm biến `app`) **trước khi** uvicorn áp dụng cấu hình log của nó, và cấu hình đó gắn lại handler cho uvicorn. Vì vậy `setup_logging` được gọi hai lần: trong `create_app()` (cho test và script) và trong lifespan (lúc này uvicorn đã cấu hình xong). Vài dòng uvicorn in ra trước lifespan (`Started server process`) vẫn giữ format cũ.

## 8. Test cho log

`caplog` giữ lại các `LogRecord`. `record_fields(record)` trả về đúng những field mà formatter sẽ ghi ra (context + extra, đã che secret):

```python
[record] = records(caplog, "Agent stream failed")
assert record.exc_info is not None
assert record_fields(record)["conversation_id"] == events[0][1]["conversation_id"]
```

Nên viết test cho những **lời hứa**:
- request id được trả về và có trong log,
- context đi theo vào background task,
- lỗi có đủ id để tra cứu,
- secret và PII không bao giờ xuất hiện trong log.

Không cần test từng dòng `logger.info`.

## 9. Đọc log: CloudWatch Logs Insights

Log JSON cho phép query theo field:

```sql
-- Mọi thứ đã xảy ra với request user báo lỗi
fields @timestamp, level, logger, message, exception
| filter request_id = "3fe999fe193b4c2e8529688d4551cf31"
| sort @timestamp asc

-- Endpoint nào chậm? (p95 theo path)
filter message = "Request finished"
| stats pct(total_ms, 95) as p95, count(*) as n by http_method, http_path
| sort p95 desc

-- Đăng nhập thất bại theo lý do, mỗi 5 phút
filter message = "Login failed"
| stats count(*) by reason, bin(5m)
```

## ⚠️ Lỗi thường gặp

- **f-string trong message** → không group được, không lọc được theo field.
- **Log 4xx ở `ERROR`** → cảnh báo vô nghĩa, người trực bắt đầu phớt lờ cảnh báo.
- **Log-and-reraise** → một lỗi thành nhiều dòng ERROR, khó đếm đúng số sự cố.
- **`BaseHTTPMiddleware` cho request id** → context mà endpoint bind không quay ra được, và có thể gây vấn đề với SSE.
- **Reset context trong middleware** → log của handler 500 mất `request_id`.
- **Dependency `def` gọi `bind_log_context`** → bind xảy ra trong thread, nên các dòng log sau đó của request không có field đó.
- **`run_in_executor` thay vì `asyncio.to_thread`** → code trong thread không thấy context.
- **Traceback nhiều dòng ở dạng text** → CloudWatch cắt thành nhiều event.
- **Tin vào redaction** → regex không bắt được mọi thứ; đừng đưa secret vào log ngay từ đầu.

## 🧪 Thực hành

1. Chạy backend với `LOG_FORMAT=json`, gọi `curl -H "X-Request-ID: test-1" localhost:8000/api/v1/auth/me` và tìm `test-1` trong log. Thử gửi một id có dấu cách rồi xem điều gì xảy ra.
2. Đổi `get_current_user` thành `def` (bỏ `async`, gọi DB kiểu khác cho tạm) và chạy `test_context_follows_the_upload_into_background_ingestion`. Giải thích vì sao test fail, rồi hoàn tác.
3. Viết test: dòng `Request finished` của `/chat/stream` có `conversation_id`.
4. Frontend: khi API trả lỗi 5xx, hiển thị "Mã lỗi: …" lấy từ header `X-Request-ID` (Next.js proxy chuyển header này về cho trình duyệt).
5. Viết một query Logs Insights tìm 10 lần ingest chậm nhất trong ngày, kèm `document_id` và `size_bytes`.

---

⬅️ Trước: [01 · Continuous Integration](01_ContinuousIntegration.md)
