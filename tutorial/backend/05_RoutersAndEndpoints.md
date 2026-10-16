# 05 · Routers & Endpoints

## 🎯 Mục tiêu

- Tổ chức endpoint theo **feature** với `APIRouter` và version hoá API (`/api/v1`).
- Nắm toàn bộ API của Document Assistant: health, auth, documents, chat.
- Chọn **status code** đúng nghĩa: `201`, `202`, `204`, `401`, `404`, `413`, `503`.
- Giữ endpoint **mỏng**: chỉ lo HTTP, logic nằm ở service (bài 11).

## 📁 File liên quan

- `backend/app/api/v1/router.py`
- `backend/app/api/v1/endpoints/{health,auth,documents,chat}.py`

---

## 1. Gom router

```python
"""Aggregate all v1 routers. Adding a feature = add a module in endpoints/ + one line here."""

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(documents.router)
api_router.include_router(chat.router)
```

`main.py` gắn `api_router` với prefix `settings.api_v1_prefix` (`/api/v1`). Khi cần thay đổi phá vỡ tương thích, tạo `api/v2/` song song, client cũ vẫn chạy.

Mỗi module tự khai báo prefix + tag (tag nhóm endpoint trong `/docs`):

```python
router = APIRouter(prefix="/documents", tags=["documents"])
```

## 2. Bản đồ API

| Method | Path | Status thành công | Auth |
| --- | --- | --- | --- |
| GET | `/health` | 200 | – |
| GET | `/health/ready` | 200 / **503** | – |
| POST | `/auth/register` | **201** | – |
| POST | `/auth/login` | 200 (+ set cookie) | – |
| POST | `/auth/refresh` | 200 | refresh token |
| POST | `/auth/logout` | **204** | – |
| GET | `/auth/me` | 200 | ✅ |
| POST | `/documents` | **202** | ✅ |
| GET | `/documents`, `/documents/{id}` | 200 | ✅ |
| GET | `/documents/{id}/download` | 200 (presigned URL) | ✅ |
| DELETE | `/documents/{id}` | **204** | ✅ |
| POST | `/chat/stream` | 200 (SSE) | ✅ |
| GET | `/conversations`, `/conversations/{id}/messages` | 200 | ✅ |
| DELETE | `/conversations/{id}` | **204** | ✅ |

## 3. Ý nghĩa các status code

```python
@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, service: AuthServiceDep) -> UserResponse:
    user = await service.register(body.email, body.password, body.full_name)
    return UserResponse.model_validate(user)
```

- **201 Created**: tạo xong một tài nguyên mới.

```python
@router.post("", response_model=DocumentResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    file: UploadFile,
    user: CurrentUser,
    service: DocumentServiceDep,
    ingestion: IngestionDep,
    background_tasks: BackgroundTasks,
) -> DocumentResponse:
    """Store the file in S3 and start ingestion in the background.

    202 Accepted = "received, still processing": poll the document until
    `status` is `ready` (or `failed`).
    """
    document = await service.upload(user, file)
    background_tasks.add_task(ingestion.run, document.id)
    return DocumentResponse.model_validate(document)
```

- **202 Accepted**: "đã nhận, đang xử lý". Upload lưu file lên S3 rồi trả về ngay; việc cắt chunk + tạo embedding (chậm, gọi API embedding) chạy sau khi response đã gửi ([agent/05](../agent/05_IngestionPipeline.md)). Client poll `GET /documents/{id}` tới khi `status` là `ready`/`failed` — frontend làm đúng như vậy ([frontend/12](../frontend/12_DocumentsPage.md)).
- `file: UploadFile` → FastAPI đọc `multipart/form-data` (cần package `python-multipart`, có sẵn trong `fastapi[standard]`).

```python
@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID, user: CurrentUser, service: DocumentServiceDep
) -> None:
    await service.delete(user, document_id)
```

- **204 No Content**: thành công, không có body. Hàm trả `None`.
- `document_id: uuid.UUID`: path param có kiểu → `/documents/abc` tự động trả **422**, service không bao giờ nhận id rác.

Các mã lỗi (`401`, `403`, `404`, `409`, `413`, `422`) không viết trong endpoint mà đến từ exception của service (bài 14).

## 4. Endpoint có response tuỳ biến: login & refresh

```python
@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest, response: Response, service: AuthServiceDep, settings: SettingsDep
) -> TokenResponse:
    user = await service.authenticate(body.email, body.password)
    tokens = await service.issue_tokens(user)
    _set_auth_cookies(response, tokens, settings)
    return TokenResponse(access_token=tokens.access_token, expires_in=tokens.access_expires_in)
```

Khai báo tham số `response: Response` để **thêm header/cookie** vào response mà FastAPI sẽ tạo từ giá trị trả về. Cookie, JWT, CSRF → bài 10.

## 5. Download: không stream file qua API

```python
@router.get("/{document_id}/download", response_model=DownloadUrlResponse)
async def download_document(
    document_id: uuid.UUID, user: CurrentUser, service: DocumentServiceDep
) -> DownloadUrlResponse:
    """Return a short-lived presigned S3 URL: the file never streams through our API."""
    url = await service.download_url(user, document_id)
    return DownloadUrlResponse(url=url, expires_in=PRESIGNED_URL_TTL_SECONDS)
```

API chỉ kiểm tra quyền rồi trả một URL ký sẵn, hết hạn sau 5 phút; trình duyệt tải thẳng từ S3. Vì sao → bài 12.

## 6. Chat: SSE + conversation

```python
@router.post(
    "/chat/stream",
    response_class=EventSourceResponse,
    dependencies=[Depends(_no_transform)],
)
async def chat_stream(
    body: ChatRequest,
    user: CurrentUser,
    conversation: ChatConversation,
    service: ChatServiceDep,
) -> AsyncIterator[ServerSentEvent]:
```

Endpoint là một **async generator**: mỗi `yield` là một event gửi ngay cho client. `ChatConversation` là dependency tìm/tạo conversation *trước* khi stream bắt đầu (bài 09), `_no_transform` đặt header chống buffer (bài 13).

## 7. Health: liveness vs readiness

```python
@router.get("", response_model=HealthResponse)
async def liveness(settings: SettingsDep) -> HealthResponse:
    """Is the process alive? Used by the orchestrator to decide on restarts."""

@router.get("/ready", response_model=ReadinessResponse)
async def readiness(request: Request, response: Response) -> ReadinessResponse:
    """Can we serve traffic? Used by the load balancer to route (or not) to this instance."""
```

Hai câu hỏi khác nhau → hai endpoint. Chi tiết ở bài 14.

## 8. Endpoint mỏng

Quy tắc: endpoint chỉ (1) nhận input đã validate, (2) gọi **một** method của service, (3) chuyển kết quả thành schema response. Không SQL, không boto3, không LangGraph trong `endpoints/`. Nhờ vậy cùng logic dùng được ở `scripts/run_agent.py` hay một worker.

## ⚠️ Lỗi thường gặp

- **Trả `200` cho mọi thứ** → client không phân biệt được "đã tạo" với "đang xử lý".
- **Trả ORM object thẳng** mà không qua schema → lộ field nội bộ (`hashed_password`, `s3_key`). Luôn khai báo `response_model` (bài 06).
- **`status_code=204` nhưng vẫn `return {...}`** → body bị bỏ / lỗi. 204 phải trả `None`.
- **Quên `include_router`** cho module mới → 404 dù code đúng.

## 🧪 Thực hành

1. Mở `/docs`, bấm "Authorize", dán `access_token` lấy từ `POST /auth/login` và gọi `GET /documents`.
2. Gọi `GET /api/v1/documents/not-a-uuid` và đọc body lỗi 422.
3. Thêm endpoint `PATCH /conversations/{id}` để đổi `title` (schema request có `title: str` tối đa 200 ký tự, trả `ConversationResponse`).

---

⬅️ Trước: [04 · App Factory & Lifespan](04_AppFactoryAndLifespan.md) · ➡️ Tiếp: [06 · Pydantic Schemas](06_PydanticSchemas.md)
