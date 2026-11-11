# ADR 0003: Logging có cấu trúc với request context

- **Trạng thái**: Đã chấp nhận
- **Ngày**: 2026-11-11
- **Liên quan**: `backend/app/core/logging.py`, `backend/app/core/middleware.py`, [tutorial/platform/05](../../tutorial/platform/05_Observability.md), `todo.md` §4a

## Bối cảnh

Trước thay đổi này, backend dùng `logging.basicConfig` với một dòng text. Khi nhiều request chạy song song, không có cách nào biết một dòng `Agent stream failed` thuộc request nào, của user nào hay conversation nào. Log text cũng không query theo field được trên CloudWatch Logs Insights. Uvicorn còn in access log riêng với format khác. Một số sự kiện quan trọng (upload, xoá tài liệu, đăng nhập) không để lại dòng log nào.

## Các quyết định

### 1. Dùng stdlib `logging`, chưa dùng structlog

| Phương án | Ưu | Nhược |
| --- | --- | --- |
| **stdlib + formatter JSON tự viết** | Không thêm dependency; log của thư viện (uvicorn, httpx, psycopg) đi chung một đường; khoảng 150 dòng, đọc hiểu được hết | Phải tự viết formatter và phần context |
| structlog | API bind context sẵn có, processor chain mạnh | Thêm một khái niệm để học; vẫn phải nối với stdlib cho log của thư viện |
| loguru | Dễ dùng | Thay thế cả hệ thống logging; khó tích hợp với log của thư viện |

Dự án này dùng để học, nên ưu tiên hiểu cơ chế bên dưới. Có thể chuyển sang structlog sau mà không phải đổi các lời gọi log, vì quy ước vẫn là `logger.info("message cố định", extra={...})`.

### 2. Context bằng `ContextVar` + record factory

`request_id`, `user_id`, `conversation_id`, `document_id` được bind vào một `ContextVar` và tự xuất hiện trên mọi record. Ta dùng record factory chứ không dùng `logging.Filter`, vì factory chạy cho mọi handler (kể cả `caplog`), còn filter gắn vào logger thì không chạy cho record được propagate từ logger con. Context được giữ trong **một** attribute `context`, để tránh `KeyError` khi `extra=` dùng trùng tên field.

### 3. Middleware ASGI thuần, không reset context

`BaseHTTPMiddleware` chạy app trong task khác, nên context mà dependency bind sẽ không quay ra được middleware. Middleware không reset context, vì handler 500 chạy trong `ServerErrorMiddleware`, nằm bên ngoài mọi user middleware, và vẫn cần `request_id`. Uvicorn tạo một task mới cho mỗi request, nên context không lọt sang request sau.

### 4. Chính sách nội dung log

- 4xx không được log ở `ERROR`. Status đã có trong dòng log của request.
- Lỗi được log một lần, ở nơi xử lý nó.
- Không log secret, email, tên file, nội dung tài liệu, prompt hay query string.
- Redaction theo tên field và theo mẫu JWT/Bearer đóng vai trò lưới an toàn, có test đi kèm.

### 5. Thay access log của uvicorn

`uvicorn.access` bị tắt. `Request finished` (có context, `ttfb_ms`, `total_ms`) thay cho nó. `setup_logging` chạy cả trong `create_app()` và trong lifespan, vì `fastapi run` import app trước khi uvicorn áp dụng cấu hình log của nó.

## Hệ quả

- Từ một `X-Request-ID` (header có trên mọi response, kể cả 500), ta tra được mọi dòng log của request đó, kể cả ingestion chạy nền sau khi upload.
- Production cần đặt `LOG_FORMAT=json`. Mặc định vẫn là `text` để dev dễ đọc.
- Vài dòng log uvicorn in ra trước lifespan (`Started server process`) vẫn ở format của uvicorn.
- Chưa có route template trong log request: FastAPI 0.143 không có API công khai cho template đầy đủ. Sẽ làm cùng OpenTelemetry (`todo.md` §4a).
- Context không tự đi sang worker riêng (§3a): phải gửi `request_id` kèm message.
