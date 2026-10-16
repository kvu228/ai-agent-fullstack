# 09 · Dependency Injection

## 🎯 Mục tiêu

- Hiểu `Depends` của FastAPI: một hàm khai báo "tôi cần gì", FastAPI lo việc tạo và dọn dẹp.
- Viết alias `Annotated[X, Depends(...)]` cho chữ ký endpoint ngắn gọn.
- Xây **chuỗi dependency**: request → session → user hiện tại → service.
- Biết vì sao `get_chat_conversation` phải là dependency chứ không nằm trong endpoint.
- Thay dependency trong test bằng `app.dependency_overrides`.

## 📁 File liên quan

- `backend/app/api/deps.py` (toàn bộ bài này)
- `backend/tests/conftest.py` (`dependency_overrides`)

---

## 1. Ý tưởng

Endpoint không tự tạo session DB, không tự đọc cookie, không tự khởi tạo service. Nó **khai báo** mình cần gì:

```python
@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: uuid.UUID, user: CurrentUser, service: DocumentServiceDep
) -> DocumentResponse:
```

FastAPI đọc chữ ký, tìm ra cây dependency, gọi từng hàm theo thứ tự, truyền kết quả vào. Lợi ích: endpoint ngắn, logic xác thực viết **một lần**, test thay được bất kỳ mắt xích nào.

## 2. Tài nguyên dùng chung: từ `app.state` sang dependency

```python
async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request; always closed, even if the endpoint raises."""
    async with request.app.state.session_factory() as session:
        yield session


def get_storage(request: Request) -> S3Storage:
    storage: S3Storage = request.app.state.storage
    return storage
```

- `get_db` là **dependency có `yield`**: code trước `yield` chạy trước endpoint, code sau (`async with` đóng session) chạy sau — kể cả khi endpoint ném lỗi. Không bao giờ rò rỉ kết nối.
- `get_storage`, `get_vector_store`, `get_ingestion`, `get_graph` chỉ lấy object đã tạo trong lifespan (bài 04). Endpoint không bao giờ đụng `app.state` trực tiếp.

```python
DbSession = Annotated[AsyncSession, Depends(get_db)]
StorageDep = Annotated[S3Storage, Depends(get_storage)]
VectorStoreDep = Annotated[DocumentVectorStore, Depends(get_vector_store)]
IngestionDep = Annotated[IngestionPipeline, Depends(get_ingestion)]
GraphDep = Annotated[CompiledStateGraph[Any, Any], Depends(get_graph)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
```

`Annotated` gộp **kiểu** (cho mypy, editor) và **cách lấy** (cho FastAPI) vào một tên. Viết `db: DbSession` thay vì `db: AsyncSession = Depends(get_db)` ở hàng chục chỗ.

## 3. Người dùng hiện tại

```python
# auto_error=False: a missing header is fine, the token may come from the cookie instead.
# Declaring it also adds the "Authorize" button to Swagger UI (/docs).
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    if credentials is not None:
        token = credentials.credentials
    elif cookie := request.cookies.get(ACCESS_COOKIE):
        _check_csrf_origin(request, settings)
        token = cookie
    else:
        raise AuthenticationError("Not authenticated")
```

Hai nguồn token, theo thứ tự:

1. Header `Authorization: Bearer ...` — cho CLI, Swagger UI, service khác.
2. Cookie `access_token` — cho trình duyệt. Chỉ nhánh này cần kiểm tra CSRF (bài 10).

```python
    try:
        claims = decode_access_token(token, settings)
        user_id = uuid.UUID(claims["sub"])
    except (jwt.InvalidTokenError, ValueError):
        # Expired, tampered or malformed: the client should call /auth/refresh.
        raise AuthenticationError("Invalid or expired access token") from None

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("User not found or inactive")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
```

- JWT hợp lệ chưa đủ: vẫn tải `User` từ DB để tài khoản bị khoá (`is_active=False`) mất quyền **ngay**, không phải chờ token hết hạn.
- Mọi lỗi đều là `AuthenticationError` → **401** với body thống nhất (bài 14). Frontend thấy 401 thì gọi `/auth/refresh`.
- Bảo vệ một endpoint = thêm tham số `user: CurrentUser`. Quên tham số này = endpoint công khai, nên review kỹ.

## 4. Service factory

```python
def get_document_service(
    db: DbSession, storage: StorageDep, vector_store: VectorStoreDep, settings: SettingsDep
) -> DocumentService:
    return DocumentService(db, storage, vector_store, settings)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]
ChatServiceDep = Annotated[ChatService, Depends(get_chat_service)]
```

Service là class Python thuần nhận mọi thứ qua constructor (bài 11) — không biết gì về FastAPI. Dependency chỉ là "keo dán".

### Cache theo request

Trong một request, `get_current_user` và `get_document_service` đều cần `DbSession`. FastAPI **cache** kết quả của mỗi dependency trong phạm vi một request → cả hai nhận **cùng một session**. Đó là điều ta muốn: user được tải và document được ghi trong cùng một session/transaction.

## 5. Dependency chạy *trước* khi stream

```python
async def get_chat_conversation(
    body: ChatRequest, user: CurrentUser, service: ChatServiceDep
) -> Conversation:
    """Resolve (or create) the conversation BEFORE the SSE stream starts.

    Once streaming begins the 200 status is already sent, so a 404 for someone
    else's conversation must happen here, in a dependency.
    """
    return await service.get_or_create_conversation(user, body.conversation_id, body.message)


ChatConversation = Annotated[Conversation, Depends(get_chat_conversation)]
```

Endpoint `/chat/stream` là async generator: khi dòng `yield` đầu tiên chạy, status `200` và header đã gửi đi. Nếu kiểm tra quyền sở hữu conversation **bên trong** generator, ta không thể trả 404 nữa. Dependency chạy xong **trước** khi response bắt đầu → ném `NotFoundError` vẫn ra 404 đúng nghĩa. Dependency cũng đọc được `body` (FastAPI parse JSON một lần, dùng chung).

## 6. Thay thế trong test

```python
app = create_app()
app.dependency_overrides[get_settings] = lambda: settings
```

`dependency_overrides` thay **bất kỳ** dependency nào: settings, user hiện tại, service… mà không sửa code app. `conftest.py` dùng nó để ép settings test, và gán `app.state` bằng S3 giả (moto), Chroma in-memory, LLM giả (bài 15).

## ⚠️ Lỗi thường gặp

- **Gọi `Depends(get_db)` ở chỗ không phải tham số** (ví dụ trong thân hàm) → nhận về object `Depends`, không phải session.
- **Tự `async_sessionmaker()()` trong endpoint** → session không được đóng khi lỗi, rò kết nối.
- **Dùng `get_db` cho background task**: session của request đã đóng khi task chạy. Task phải tự mở session từ `session_factory` ([agent/05](../agent/05_IngestionPipeline.md)).
- **`HTTPBearer()` mặc định (`auto_error=True`)** → request chỉ có cookie bị trả 401 ("Not authenticated", định dạng `detail` của FastAPI chứ không phải `{"error": ...}`) trước khi kịp đọc cookie.

## 🧪 Thực hành

1. Viết dependency `get_ready_document(document_id, user, service)` trả `Document` hoặc ném `ValidationFailedError` nếu `status != ready`; dùng nó trong một endpoint mới.
2. Trong một test, đặt `app.dependency_overrides[get_current_user] = lambda: fake_user` và gọi `/auth/me` không cần login.
3. Thêm `print(id(db))` tạm thời vào `get_current_user` và `get_document_service`; xác nhận cùng một session trong một request.

---

⬅️ Trước: [08 · Alembic Migrations](08_AlembicMigrations.md) · ➡️ Tiếp: [10 · Authentication](10_Authentication.md)
