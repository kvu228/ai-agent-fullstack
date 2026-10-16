# 06 · Pydantic Schemas — API Contract

## 🎯 Mục tiêu

- Phân biệt **schema** (hợp đồng API) với **model** (bảng DB).
- Validate input ngay tại biên: email, độ dài mật khẩu, độ dài tin nhắn.
- Chuẩn hoá dữ liệu bằng `field_validator` (email viết thường).
- Xây response từ ORM object bằng `from_attributes` và **không bao giờ** lộ field nội bộ.

## 📁 File liên quan

- `backend/app/schemas/{auth,document,chat,health}.py`
- `frontend/src/types/api.ts` (bản phản chiếu bằng TypeScript)

---

## 1. Schema ≠ Model

| | `app/models/` (SQLAlchemy) | `app/schemas/` (Pydantic) |
| --- | --- | --- |
| Mô tả | Bảng trong Postgres | Dữ liệu đi qua HTTP |
| Ví dụ field | `hashed_password`, `s3_key`, `owner_id` | `email`, `filename`, `status` |
| Ai thấy | Chỉ backend | Client (công khai trong `/docs`) |

Giữ hai thứ tách biệt cho phép đổi DB mà không phá API, và **mặc định không lộ** gì cả: một field chỉ ra ngoài khi bạn thêm nó vào schema response.

## 2. Auth schemas

```python
Password = Annotated[str, Field(min_length=8, max_length=128)]


class RegisterRequest(BaseModel):
    email: EmailStr
    password: Password
    full_name: Annotated[str | None, Field(max_length=200)] = None

    @field_validator("email")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        # "Alice@Example.com" and "alice@example.com" must be the same account.
        return value.strip().lower()
```

- `EmailStr` (cần `email-validator`, có trong `fastapi[standard]`) từ chối `"abc"` với 422.
- `Password` là **type alias có ràng buộc**: tái sử dụng ở mọi nơi cần mật khẩu. `max_length=128` không chỉ cho đẹp — hash Argon2 một chuỗi 10 MB là một cách DoS server.
- `normalise_email`: không chuẩn hoá thì một người có thể đăng ký hai tài khoản `Alice@x.com` và `alice@x.com`. `LoginRequest` có validator y hệt để đăng nhập bằng chữ hoa vẫn đúng.

```python
class TokenResponse(BaseModel):
    """Returned on login/refresh for non-browser clients (CLI, Swagger UI).

    Browsers don't need it: the same tokens are set as httpOnly cookies.
    """

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    # from_attributes: build directly from the SQLAlchemy `User` object.
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str | None
    created_at: datetime
```

`from_attributes=True` cho phép `UserResponse.model_validate(user)` đọc thuộc tính của ORM object. `hashed_password` và `is_active` không có trong schema → không bao giờ ra ngoài. Test kiểm chứng: `assert "hashed_password" not in response.json()`.

Lưu ý `TokenResponse` **không** chứa refresh token: refresh token chỉ đi trong cookie httpOnly (hoặc client gửi lại trong body khi refresh). Bài 10 giải thích.

## 3. Document schemas

```python
class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    status: DocumentStatus
    chunk_count: int
    error: str | None
    created_at: datetime
    # Note: `s3_key` and `owner_id` are deliberately NOT exposed.


class DownloadUrlResponse(BaseModel):
    url: str
    expires_in: int
```

- `status: DocumentStatus` dùng chung enum với model (`processing | ready | failed`) → OpenAPI liệt kê đúng 3 giá trị, frontend sinh type `DocumentStatus` khớp.
- **Không lộ `s3_key`**: key chứa `user_id` và cấu trúc bucket — thông tin nội bộ giúp kẻ tấn công dò hệ thống. Client chỉ cần `id`; muốn tải file thì xin presigned URL. Test: `assert "s3_key" not in document`.
- Upload không có schema request: file đến qua `UploadFile` (multipart), validate trong service (bài 11).

## 4. Chat schemas

```python
class ChatRequest(BaseModel):
    message: Annotated[str, Field(min_length=1, max_length=4000)]
    conversation_id: Annotated[
        uuid.UUID | None,
        Field(description="Omit to start a new conversation."),
    ] = None
```

- `max_length=4000`: giới hạn chi phí token và chống payload khổng lồ.
- `description` hiện trong `/docs`, là tài liệu cho người gọi API.

```python
StreamEventType = Literal["metadata", "token", "tool_start", "tool_end", "done", "error"]


class StreamEvent(BaseModel):
    """One SSE event. `type` is sent as the SSE `event:` field, the rest as JSON `data:`."""

    type: StreamEventType
    conversation_id: uuid.UUID | None = None
    content: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, object] | None = None
```

Một schema cho mọi loại event, field nào không dùng thì `None` và bị loại khi gửi (`exclude_none=True`, bài 13). Frontend mô hình hoá chính xác hơn bằng discriminated union theo `type` ([frontend/04](../frontend/04_TypesAndConfig.md)).

`ConversationResponse` (id, title, created_at, updated_at) và `ConversationMessages` (`conversation_id`, `messages: list[ChatMessage]`) dùng cho sidebar và lịch sử.

## 5. Health schemas

```python
class ReadinessResponse(BaseModel):
    """Readiness: can we serve traffic? Checks every backing service."""

    status: Literal["ok", "degraded"]
    checks: dict[str, bool]
```

## 6. Hợp đồng hai phía

```python
"""Request/response contracts for chat and conversations.

Schemas are the public contract between backend and frontend; the frontend
mirrors them in `frontend/src/types/api.ts`.
"""
```

Sửa schema backend → sửa `types/api.ts` trong cùng commit. Dự án lớn hơn có thể sinh type TypeScript tự động từ `/openapi.json`.

## ⚠️ Lỗi thường gặp

- **Dùng ORM model làm `response_model`** hoặc trả `user.__dict__` → lộ `hashed_password`.
- **Quên `from_attributes=True`** → `model_validate(orm_obj)` báo lỗi "Input should be a valid dictionary".
- **Validate trong endpoint bằng `if`** thay vì trong schema → lỗi không đồng nhất, `/docs` không mô tả ràng buộc.
- **Đổi tên field ở backend** mà quên frontend → UI hiển thị `undefined` mà không báo lỗi gì.

## 🧪 Thực hành

1. Gọi `POST /auth/register` với `password` 7 ký tự và đọc chi tiết 422 (`loc`, `msg`).
2. Thêm `updated_at` vào `DocumentResponse`, cập nhật `frontend/src/types/api.ts` cho khớp.
3. Viết test: đăng ký `"Bob@Example.COM"` rồi đăng nhập bằng `"bob@example.com"` phải thành công.

---

⬅️ Trước: [05 · Routers & Endpoints](05_RoutersAndEndpoints.md) · ➡️ Tiếp: [07 · Database với SQLAlchemy](07_DatabaseSQLAlchemy.md)
