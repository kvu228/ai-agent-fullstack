# 07 · Database với SQLAlchemy 2 (async)

## 🎯 Mục tiêu

- Khai báo bảng bằng SQLAlchemy 2 kiểu mới: `Mapped[...]` + `mapped_column`.
- Dùng **mixin** cho phần lặp lại (UUID primary key, `created_at`/`updated_at`).
- Hiểu **naming convention** cho constraint và vì sao Alembic cần nó.
- Tạo **async engine** + **session factory**, mỗi request một session.
- Biết các bẫy của async ORM: lazy loading, `expire_on_commit`.

## 📁 File liên quan

- `backend/app/db/base.py`, `backend/app/db/session.py`
- `backend/app/models/{user,refresh_token,document,conversation}.py`, `backend/app/models/__init__.py`

---

## 1. Base + naming convention

```python
# Deterministic constraint names -> Alembic can generate clean, reversible migrations.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
```

Không có convention, Postgres tự đặt tên constraint (`documents_owner_id_fkey`…) và mỗi DB có thể khác nhau. Muốn **xoá/sửa** một constraint trong migration sau này, bạn phải biết tên chính xác của nó. Có convention → tên luôn là `fk_documents_owner_id_users`, viết `downgrade()` dễ dàng (bài 08). Đặt convention **ngay từ đầu** — đổi sau rất khổ.

## 2. Mixin dùng lại

```python
class UUIDPrimaryKeyMixin:
    # UUIDs (not auto-increment ints) are safe to expose in URLs: not guessable.
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), onupdate=utcnow
    )
```

- **UUID thay vì `SERIAL`**: `/documents/42` mời người ta thử `/documents/43`. UUID không đoán được (dù vậy vẫn phải kiểm tra quyền sở hữu — bài 11). UUID còn tạo được *trước* khi insert, nên `DocumentService` dùng luôn id đó để đặt S3 key.
- `DateTime(timezone=True)` = `TIMESTAMPTZ`. Luôn lưu thời gian có múi giờ (UTC).
- `default=utcnow` (phía Python) **và** `server_default=func.now()` (phía DB): insert từ ORM hay từ SQL tay đều có giá trị.
- `onupdate=utcnow`: tự cập nhật khi ORM ghi thay đổi.

## 3. `Mapped[...]` — type hint là schema

```python
class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(200))
    # Only ever store a slow, salted hash (argon2) — never the password itself.
    hashed_password: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
```

- `Mapped[str]` → `NOT NULL`; `Mapped[str | None]` → cho phép `NULL`. mypy hiểu kiểu của `user.email`.
- `unique=True` trên `email` là **nguồn sự thật** chống trùng: service bắt `IntegrityError` thay vì "SELECT rồi INSERT" (cách đó bị race khi hai request đăng ký cùng lúc — bài 11).
- `String(320)`: độ dài tối đa của địa chỉ email theo RFC.

## 4. Các model còn lại

```python
class RefreshToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

Lưu **hash** SHA-256 (64 ký tự hex) của token, không lưu token. `revoked_at` thay vì xoá dòng → phát hiện được token cũ bị dùng lại (bài 10).

```python
class DocumentStatus(enum.StrEnum):
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Metadata of an uploaded file. The bytes live in S3, the chunks in Chroma."""

    __tablename__ = "documents"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    s3_key: Mapped[str] = mapped_column(String(1024), unique=True)
    status: Mapped[DocumentStatus] = mapped_column(
        # native_enum=False -> plain VARCHAR (no Postgres ENUM type): adding a status later
        # needs no ALTER TYPE. Add create_constraint=True if the DB itself must reject bad values.
        Enum(
            DocumentStatus,
            native_enum=False,
            length=20,
            values_callable=lambda e: [m.value for m in e],
        ),
        default=DocumentStatus.PROCESSING,
    )
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
```

- Postgres giữ **metadata**; bytes ở S3 (bài 12); chunk/vector ở Chroma ([agent/04](../agent/04_EmbeddingsAndVectorStore.md)). Mỗi store làm đúng việc nó giỏi.
- `native_enum=False`: Postgres có kiểu `ENUM` riêng, nhưng thêm giá trị mới cần `ALTER TYPE`, khó migrate. Ở đây cột chỉ là `VARCHAR(20)`, thêm status mới không cần migration nào.
- ⚠️ Từ SQLAlchemy 2.0, `Enum(..., native_enum=False)` **không** tạo `CHECK` trừ khi đặt `create_constraint=True` (nhiều tài liệu cũ nói ngược lại). Kiểm chứng bằng `\d documents`: không có constraint `ck_...` nào. Hiện tại tính hợp lệ chỉ dựa vào code Python (mọi chỗ gán `status` đều dùng `DocumentStatus.*`, mypy kiểm tra kiểu). Muốn DB tự chặn, thêm `create_constraint=True` (và `name="document_status"` để naming convention `ck` có `constraint_name`).
- `values_callable`: lưu `"ready"` (value) thay vì `"READY"` (tên) — khớp với JSON API.
- `BigInteger` cho `size_bytes`: `Integer` 32-bit tối đa ~2 GB.
- `index=True` trên `owner_id`: **mọi** truy vấn đều lọc theo chủ sở hữu.

```python
class Conversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Ownership + title of a chat thread.

    The messages themselves are stored by the LangGraph Postgres checkpointer,
    keyed by `thread_id == str(conversation.id)`.
    """

    __tablename__ = "conversations"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
```

Bảng `conversations` chỉ lưu **ai sở hữu** và tiêu đề; nội dung tin nhắn nằm trong bảng checkpoint của LangGraph ([agent/10](../agent/10_MemoryPostgresCheckpointer.md)).

### Không dùng `relationship()`?

Các model chỉ khai báo `ForeignKey(..., ondelete="CASCADE")`, không có `relationship()`. Đó là lựa chọn có chủ đích: service luôn truy vấn rõ ràng (`select(Document).where(Document.owner_id == ...)`), và tránh được **lazy loading** — thứ bị cấm trong async (xem mục 6). `ondelete="CASCADE"` để Postgres tự xoá token/document/conversation khi xoá user. Khi cần `relationship()`, hãy dùng kèm `lazy="raise"` hoặc `selectinload()`.

## 5. Đăng ký mọi model

```python
"""Import every model here so `Base.metadata` knows all tables (needed by Alembic)."""

from app.models.conversation import Conversation
from app.models.document import Document, DocumentStatus
from app.models.refresh_token import RefreshToken
from app.models.user import User
```

Một class chỉ được thêm vào `Base.metadata` khi module của nó **được import**. Alembic import `app.models` để thấy đủ bảng (bài 08).

## 6. Engine & session

```python
def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        pool_pre_ping=True,  # drop dead connections (e.g. after an RDS failover)
        echo=settings.db_echo,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: objects stay usable after commit (needed to return them
    # from endpoints without triggering lazy loads, which async sessions forbid).
    return async_sessionmaker(engine, expire_on_commit=False)
```

- **Engine** = connection pool, tạo một lần trong lifespan (bài 04).
- `pool_pre_ping=True`: kiểm tra kết nối trước khi dùng. Sau khi RDS failover hay Postgres restart, kết nối cũ đã chết; không có pre-ping thì request đầu tiên lỗi.
- **`expire_on_commit=False`**: mặc định sau `commit()` SQLAlchemy "làm hết hạn" mọi thuộc tính; truy cập `document.filename` sau đó sẽ kích hoạt một câu SELECT ngầm. Trong async, I/O ngầm không được phép → lỗi `MissingGreenlet`. Tắt nó đi để service `commit()` rồi trả object cho endpoint an toàn.

**Một session cho mỗi request** (dependency `get_db`, bài 09): session không thread-safe, không chia sẻ giữa các request. Background task (ingestion) tự mở session riêng từ `session_factory`.

## ⚠️ Lỗi thường gặp

- **`MissingGreenlet` / "greenlet_spawn has not been called"**: lazy load trong async. Kiểm tra `expire_on_commit` hoặc dùng `selectinload`.
- **Dùng `datetime.utcnow()`** (naive, deprecated) → so sánh với giá trị `TIMESTAMPTZ` lỗi. Dùng `datetime.now(UTC)` như `utcnow()`.
- **Quên import model mới trong `models/__init__.py`** → Alembic autogenerate không thấy bảng.
- **Dùng chung một `AsyncSession` cho nhiều task song song** → lỗi "concurrent operations are not permitted".

## 🧪 Thực hành

1. `docker compose exec postgres psql -U app -d app -c '\d documents'`: tìm `pk_documents`, `uq_documents_s3_key`, `ix_documents_owner_id`, `fk_documents_owner_id_users` — tất cả đều do naming convention đặt tên.
2. Thêm cột `page_count: Mapped[int | None]` vào `Document` (tạo migration ở bài 08).
3. Trong một script, tạo session từ `create_session_factory`, đếm số tài liệu theo `status` bằng `select(Document.status, func.count()).group_by(Document.status)`.

---

⬅️ Trước: [06 · Pydantic Schemas](06_PydanticSchemas.md) · ➡️ Tiếp: [08 · Alembic Migrations](08_AlembicMigrations.md)
