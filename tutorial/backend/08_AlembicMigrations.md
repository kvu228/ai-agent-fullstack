# 08 · Alembic Migrations

## 🎯 Mục tiêu

- Hiểu vì sao schema DB phải được quản lý bằng **migration** có version, không phải `create_all()`.
- Đọc cấu hình `alembic.ini` và `migrations/env.py` (bản **async**).
- Dùng `--autogenerate` để sinh migration từ model, rồi `upgrade` / `downgrade`.
- Cùng tồn tại với các bảng do **LangGraph** tự tạo.
- Chạy migration như một **bước deploy riêng** (job `migrate` trong Docker Compose).

## 📁 File liên quan

- `backend/alembic.ini`
- `backend/migrations/env.py`, `backend/migrations/script.py.mako`
- `backend/migrations/versions/2026_10_16_1125-9dc7d3aa1ca5_initial_schema.py`
- `Makefile` (`migrate`), `docker-compose.yml` (service `migrate`)

---

## 1. Vì sao không `Base.metadata.create_all()`?

`create_all()` chỉ **tạo bảng chưa có**. Nó không thêm cột vào bảng đã tồn tại, không đổi kiểu, không xoá — và không để lại dấu vết gì. Trên RDS có dữ liệu thật, bạn cần:

- Mỗi thay đổi schema là một **file** trong git, được review như code.
- DB biết mình đang ở version nào (bảng `alembic_version`).
- Có đường lùi (`downgrade`).

Project chỉ dùng `create_all()` trong **test** (SQLite tạm, xoá sau mỗi test — bài 15).

## 2. `alembic.ini`

```ini
[alembic]
script_location = %(here)s/migrations
file_template = %%(year)d_%%(month).2d_%%(day).2d_%%(hour).2d%%(minute).2d-%%(rev)s_%%(slug)s
prepend_sys_path = .

[post_write_hooks]
hooks = ruff_fix, ruff_format
ruff_fix.type = module
ruff_fix.module = ruff
ruff_fix.options = check --fix REVISION_SCRIPT_FILENAME
ruff_format.type = module
ruff_format.module = ruff
ruff_format.options = format REVISION_SCRIPT_FILENAME
```

- `file_template`: tên file bắt đầu bằng **ngày giờ** → `ls` ra đúng thứ tự tạo (mặc định chỉ có hash ngẫu nhiên, rất khó nhìn).
- `prepend_sys_path = .`: để `env.py` import được package `app`.
- `post_write_hooks`: sau khi sinh file, chạy `ruff check --fix` rồi `ruff format` → migration sinh tự động vẫn qua được `make lint`.
- **Không có** `sqlalchemy.url` trong file ini: URL lấy từ `Settings` (một nguồn sự thật, bài 02).

## 3. `env.py` — async và đọc từ Settings

```python
import app.models  # noqa: F401  (registers every table on Base.metadata)
from app.core.config import get_settings
from app.db.base import Base

target_metadata = Base.metadata
DATABASE_URL = get_settings().database_url
```

- `import app.models` là bắt buộc: không import thì `Base.metadata` rỗng, autogenerate tưởng bạn đã xoá hết bảng (bài 07, mục 5).
- `DATABASE_URL` từ `Settings` → local, Docker, production dùng chung một cơ chế biến môi trường.

```python
async def run_async_migrations() -> None:
    engine = create_async_engine(DATABASE_URL, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()
```

App dùng driver async (`postgresql+psycopg`), nên migration cũng chạy async. Alembic bản thân là code đồng bộ → `connection.run_sync(...)` chạy hàm sync trên kết nối async. `NullPool`: chạy một lần rồi thoát, không cần pool.

## 4. Sống chung với bảng của LangGraph

```python
# Tables owned by LangGraph's checkpointer (created by `AsyncPostgresSaver.setup()`).
# Alembic must not try to drop them just because they aren't in our models.
LANGGRAPH_TABLES = {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}


def include_object(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    return not (type_ == "table" and name in LANGGRAPH_TABLES)
```

Autogenerate so sánh **model** với **DB thật**. Bảng checkpoint có trong DB nhưng không có trong model → không có `include_object`, Alembic sẽ sinh `op.drop_table("checkpoints")` — xoá sạch lịch sử chat của mọi người. Các bảng này do `checkpointer.setup()` tự tạo và tự migrate ([agent/10](../agent/10_MemoryPostgresCheckpointer.md)).

`\dt` trên DB sau khi chạy đủ sẽ thấy: `alembic_version`, 4 bảng checkpoint, và 4 bảng của app (`users`, `refresh_tokens`, `documents`, `conversations`).

```python
def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        compare_type=True,
    )
```

`compare_type=True`: phát hiện cả thay đổi **kiểu cột** (ví dụ `String(100)` → `String(255)`).

## 5. Đọc một migration

```python
revision: str = "9dc7d3aa1ca5"
down_revision: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("email", sa.String(length=320), nullable=False),
        ...
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
```

- `revision` / `down_revision` tạo thành **chuỗi liên kết**; migration đầu tiên có `down_revision = None`.
- `op.f("pk_users")`: tên đã được naming convention tính sẵn, `op.f` báo Alembic "dùng nguyên tên này". Đây là thành quả của bài 07, mục 1.
- `downgrade()` xoá theo thứ tự ngược (`refresh_tokens`, `documents`, `conversations` trước `users` vì có FK).

> Autogenerate **không hoàn hảo**: không phát hiện đổi tên cột (nó thấy "xoá cột cũ + thêm cột mới" → mất dữ liệu!), không biết data migration. **Luôn đọc lại** file trước khi commit.

## 6. Quy trình hằng ngày

```bash
# 1. Sửa model (ví dụ thêm Document.page_count)
# 2. Sinh migration
uv run alembic revision --autogenerate -m "add page_count to documents"
# 3. Đọc lại file mới trong migrations/versions/
# 4. Áp dụng
uv run alembic upgrade head        # hoặc: make migrate
# Lùi lại một bước nếu cần
uv run alembic downgrade -1
# Xem trạng thái
uv run alembic current
uv run alembic history
```

Cột mới `NOT NULL` trên bảng đã có dữ liệu cần `server_default` (hoặc làm 3 bước: thêm nullable → backfill → đặt NOT NULL), nếu không `upgrade` sẽ lỗi.

### Offline mode cho DBA

```python
def run_migrations_offline() -> None:
    """Generate SQL without connecting (`alembic upgrade head --sql`), e.g. for a DBA review."""
```

`uv run alembic upgrade head --sql > migration.sql` in ra SQL thay vì chạy — hữu ích khi production chỉ cho phép DBA chạy script.

## 7. Migration là một bước deploy riêng

```yaml
  # One-shot job: apply database migrations, then exit. The backend waits for it.
  # (In production this is a separate deploy step, never done by every app replica.)
  migrate:
    profiles: ["app"]
    build: ./backend
    command: ["alembic", "upgrade", "head"]
    depends_on:
      postgres:
        condition: service_healthy
```

Service `backend` có `depends_on: migrate: condition: service_completed_successfully` (bài 16). Vì sao không chạy migration trong lifespan của app? Chạy 4 replica → 4 tiến trình cùng migrate một lúc → race. Trên AWS, đây thường là một ECS task / Kubernetes Job chạy trước khi rollout.

Thay đổi schema nên **tương thích ngược** với version code đang chạy (thêm cột nullable trước, xoá cột ở lần deploy sau), vì trong lúc rollout, code cũ và schema mới cùng tồn tại.

## ⚠️ Lỗi thường gặp

- **Autogenerate sinh file rỗng**: quên import model trong `app/models/__init__.py`, hoặc DB đã được tạo bằng `create_all()`.
- **Autogenerate muốn drop bảng `checkpoints`**: `include_object` bị bỏ hoặc thiếu tên bảng.
- **`Target database is not up to date`**: chưa `upgrade head` trước khi `revision --autogenerate`.
- **Hai nhánh cùng tạo migration** → "Multiple head revisions". Dùng `alembic merge heads` hoặc rebase lại `down_revision`.
- **Sửa một migration đã chạy trên môi trường khác**: không làm! Tạo migration mới.

## 🧪 Thực hành

1. `uv run alembic upgrade head --sql | less` và đọc SQL Alembic sẽ chạy.
2. Thêm `page_count: Mapped[int | None]` vào `Document`, autogenerate, đọc file, `upgrade`, rồi `downgrade -1` và `upgrade` lại.
3. Thử xoá dòng `include_object=include_object` trong `do_run_migrations`, chạy `alembic check` để thấy Alembic muốn drop bảng checkpoint. **Đặt lại** ngay sau đó.

---

⬅️ Trước: [07 · Database với SQLAlchemy](07_DatabaseSQLAlchemy.md) · ➡️ Tiếp: [09 · Dependency Injection](09_DependencyInjection.md)
