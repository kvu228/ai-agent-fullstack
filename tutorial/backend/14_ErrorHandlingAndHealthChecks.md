# 14 · Error Handling & Health Checks

## 🎯 Mục tiêu

- Xây **cây exception của domain** và một handler biến chúng thành JSON nhất quán.
- Không bao giờ lộ stack trace ra ngoài; luôn ghi log lỗi bất ngờ.
- Phân biệt **liveness** và **readiness** probe.
- Viết readiness probe kiểm tra Postgres, S3, Chroma **song song** với **timeout**.

## 📁 File liên quan

- `backend/app/core/exceptions.py`
- `backend/app/api/v1/endpoints/health.py`
- `backend/app/schemas/health.py`
- `frontend/src/lib/api/client.ts` (`errorMessage` — phía đọc lỗi)

---

## 1. Exception của domain

```python
class AppError(Exception):
    """Base class for errors we raise on purpose."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "app_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
```

| Exception | HTTP | `code` | Ví dụ |
| --- | --- | --- | --- |
| `NotFoundError` | 404 | `not_found` | Tài liệu không tồn tại **hoặc** của người khác (bài 11) |
| `ConflictError` | 409 | `conflict` | Email đã đăng ký |
| `AuthenticationError` | 401 | `not_authenticated` | Token hết hạn, sai mật khẩu |
| `PermissionDeniedError` | 403 | `permission_denied` | Request cross-site (CSRF, bài 10) |
| `ValidationFailedError` | 422 | `validation_failed` | File rỗng, sai định dạng |
| `PayloadTooLargeError` | 413 | `payload_too_large` | File vượt `MAX_UPLOAD_MB` |
| `AgentError` | 502 | `agent_error` | Dành cho lỗi từ LLM provider (hiện chưa dùng) |

Service chỉ cần `raise NotFoundError("Document not found")` — không biết gì về HTTP. `code` là chuỗi máy đọc được, ổn định; `message` là cho người đọc và có thể đổi.

`HTTP_422_UNPROCESSABLE_CONTENT` và `HTTP_413_CONTENT_TOO_LARGE` là tên mới theo RFC 9110 (tên cũ `..._ENTITY` đã deprecated trong Starlette).

## 2. Handler: một định dạng cho mọi lỗi

```python
def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if isinstance(exc, AuthenticationError) else None
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
            headers=headers,
        )
```

```json
{"error": {"code": "not_found", "message": "Document not found"}}
```

- `WWW-Authenticate: Bearer` cho 401: chuẩn HTTP yêu cầu header này với 401, báo client cách xác thực.
- Một handler cho cả cây `AppError` (FastAPI chọn handler theo MRO của exception).

```python
    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error", exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": {"code": "internal_error", "message": "Internal server error"}},
        )
```

Lỗi **không** cố ý (bug, DB sập giữa chừng): log đầy đủ stack trace phía server, client chỉ nhận thông báo chung. Stack trace có thể chứa đường dẫn file, câu SQL, đôi khi cả secret.

### Ngoại lệ: lỗi validate của FastAPI

Body/params sai kiểu (bài 06) do FastAPI xử lý **trước** khi tới code của ta, trả định dạng riêng:

```json
{"detail": [{"loc": ["body", "password"], "msg": "String should have at least 8 characters", ...}]}
```

Project giữ nguyên định dạng này (Swagger UI và nhiều client đã hiểu nó); frontend đọc được cả hai:

```ts
  // The backend returns {"error": {"message": ...}} (or {"detail": [...]} for 422).
  if (typeof error?.message === "string") return error.message;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && typeof detail[0]?.msg === "string") return detail[0].msg;
```

Muốn thống nhất 100%, thêm handler cho `RequestValidationError` (xem Thực hành).

## 3. Liveness vs. readiness

| | `GET /health` (liveness) | `GET /health/ready` (readiness) |
| --- | --- | --- |
| Câu hỏi | Process còn sống không? | Có phục vụ được request không? |
| Kiểm tra | Không gì cả — trả lời được là sống | Postgres, S3, Chroma |
| Thất bại thì | Orchestrator **khởi động lại** container | Load balancer **ngừng gửi traffic** tới instance này |
| Dùng ở | Docker `HEALTHCHECK` (bài 16), K8s `livenessProbe` | ALB target group, K8s `readinessProbe` |

Liveness **không** được kiểm tra DB: Postgres chậm 10 giây → mọi container bị restart cùng lúc → sự cố nhỏ thành sập toàn bộ.

```python
@router.get("", response_model=HealthResponse)
async def liveness(settings: SettingsDep) -> HealthResponse:
    """Is the process alive? Used by the orchestrator to decide on restarts."""
    return HealthResponse(app_name=settings.app_name, environment=settings.environment)
```

## 4. Readiness: song song + timeout

```python
    async def database() -> None:
        async with state.session_factory() as session:
            await session.execute(text("SELECT 1"))

    async def storage() -> None:
        await state.storage.ensure_bucket()

    async def vector_store() -> None:
        await asyncio.to_thread(state.chroma_client.heartbeat)

    async def check(name: str, probe: Callable[[], Awaitable[None]]) -> tuple[str, bool]:
        try:
            await asyncio.wait_for(probe(), timeout=3)
            return name, True
        except Exception:
            logger.warning("Readiness check failed: %s", name, exc_info=True)
            return name, False
```

- Mỗi probe là thao tác **rẻ nhất** chứng minh service dùng được: `SELECT 1`, `head_bucket`, Chroma `heartbeat` (client Chroma đồng bộ → `to_thread`).
- `asyncio.wait_for(..., timeout=3)`: một service treo không làm readiness treo theo (load balancer thường timeout ~5 s).
- Lỗi → `False` + log cảnh báo, không ném ra ngoài.

```python
    results = await asyncio.gather(
        check("database", database), check("storage", storage), check("vector_store", vector_store)
    )
    checks = dict(results)
    healthy = all(checks.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="ok" if healthy else "degraded", checks=checks)
```

`asyncio.gather`: ba probe chạy **cùng lúc** → tổng thời gian ≈ probe chậm nhất, không phải tổng. Một service hỏng → **503** + body cho biết cái nào:

```json
{"status":"degraded","checks":{"database":true,"storage":true,"vector_store":false}}
```

> Readiness có kiểm tra OpenAI không? Không: gọi API trả phí mỗi vài giây là lãng phí, và OpenAI chậm không có nghĩa instance này hỏng. Lỗi LLM được xử lý theo từng request (event `error`, bài 13; document `failed`, [agent/05](../agent/05_IngestionPipeline.md)).

## ⚠️ Lỗi thường gặp

- **`raise HTTPException` trong service** → định dạng `{"detail": ...}` lẫn với `{"error": ...}`, và service phụ thuộc FastAPI.
- **Trả `str(exc)` của lỗi bất ngờ cho client** → lộ thông tin nội bộ.
- **Readiness không có timeout** → một DB treo làm probe treo, load balancer đánh dấu mọi instance là hỏng.
- **Liveness kiểm tra dependency** → restart dây chuyền khi một dependency chậm.
- **Nuốt exception mà không log** (`except: pass`) → lỗi biến mất không dấu vết.

## 🧪 Thực hành

1. `docker compose stop chroma` rồi `curl -i localhost:8000/api/v1/health/ready`: thấy `503` và `"vector_store": false`, đo thời gian phản hồi (≤ 3 s). Bật lại.
2. Thêm handler cho `RequestValidationError` trả `{"error": {"code": "validation_failed", "message": ..., "fields": [...]}}`; kiểm tra frontend vẫn hiển thị đúng lỗi.
3. Thêm `RateLimitedError` (429, `code="rate_limited"`) và dùng nó khi làm rate limiting cho login (bài 10).

---

⬅️ Trước: [13 · Streaming SSE](13_StreamingSSE.md) · ➡️ Tiếp: [15 · Testing](15_Testing.md)
