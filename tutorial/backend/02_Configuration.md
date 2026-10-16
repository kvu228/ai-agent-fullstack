# 02 · Configuration với pydantic-settings

## 🎯 Mục tiêu

- Gom **toàn bộ cấu hình** (DB, auth, S3, Chroma, LLM…) vào một class `Settings` có type.
- Hiểu thứ tự ưu tiên: biến môi trường > file `.env` > giá trị mặc định.
- Dùng `SecretStr` để secret không lọt vào log.
- Viết **validator** để app *từ chối khởi động* khi cấu hình nguy hiểm (secret yếu, production không HTTPS).

## 📁 File liên quan

- `backend/app/core/config.py`
- `backend/.env.example`
- `docker-compose.yml` (ghi đè biến khi chạy trong container)

---

## 1. Vì sao không `os.getenv` rải rác?

`os.getenv("CHROMA_PORT")` trả về `str | None`, không validate, mỗi nơi tự đặt default riêng. Với `pydantic-settings`:

- Mỗi biến có **kiểu** (`int`, `bool`, `list[str]`, `Literal[...]`) → sai kiểu là lỗi ngay lúc khởi động, không phải lúc 3 giờ sáng.
- Một chỗ duy nhất liệt kê *mọi* cấu hình → `.env.example` chỉ việc phản chiếu nó.
- Test tạo `Settings(...)` với giá trị riêng, không đụng biến môi trường thật (bài 15).

## 2. Class `Settings` theo nhóm

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- App ---------------------------------------------------------------
    app_name: str = "Document Assistant API"
    environment: Literal["development", "staging", "production", "test"] = "development"
    api_v1_prefix: str = "/api/v1"
    # Origins allowed by CORS *and* by the CSRF origin check (see app/api/deps.py).
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
```

Tên field không phân biệt hoa thường với biến môi trường: `CORS_ORIGINS` → `cors_origins`. Field kiểu `list[str]` được đọc dưới dạng **JSON**: `CORS_ORIGINS=["http://localhost:3000"]`.

```python
    # ---- Database (AWS RDS for PostgreSQL in production) -------------------
    database_url: str = "postgresql+psycopg://app:app@localhost:5433/app"
    db_pool_size: int = 5
    db_echo: bool = False
```

`postgresql+psycopg://` = dialect `postgresql`, driver `psycopg` (v3, async). Cổng **5433** vì máy dev hay có sẵn một Postgres ở 5432 (bài 03). Trên AWS chỉ cần đổi URL sang endpoint RDS.

```python
    # ---- Auth --------------------------------------------------------------
    jwt_secret_key: SecretStr = SecretStr(_DEV_JWT_SECRET)
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 7
    # Secure cookies are only sent over HTTPS: must be true in production.
    cookie_secure: bool = False

    # ---- Blob storage (AWS S3; Floci locally) ------------------------------
    s3_bucket: str = "documents"
    s3_endpoint_url: str | None = "http://localhost:4566"  # None -> real AWS
    # Endpoint the *browser* uses for presigned URLs (differs inside Docker networks).
    s3_public_endpoint_url: str | None = None
    aws_region: str = "us-east-1"
    aws_access_key_id: SecretStr | None = SecretStr("test")
    aws_secret_access_key: SecretStr | None = SecretStr("test")
    max_upload_mb: int = 20

    # ---- Vector store (ChromaDB) -------------------------------------------
    chroma_host: str = "localhost"
    chroma_port: int = 8001
    chroma_collection: str = "documents"

    # ---- LLM / RAG ---------------------------------------------------------
    llm_model: str = "anthropic:claude-sonnet-5-5"
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    embedding_model: str = "openai:text-embedding-3-small"
    chunk_size: int = 1000
    chunk_overlap: int = 150
    retrieval_k: int = 5

    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
```

Vài điểm đáng chú ý:

- `s3_endpoint_url = None` nghĩa là "dùng AWS thật" — boto3 tự tìm endpoint. Local thì trỏ vào Floci (bài 03, 12).
- `aws_access_key_id = None` → boto3 dùng *default credential chain* (biến môi trường, IAM role…). Trên AWS **đừng** dùng access key tĩnh; gán IAM role cho container (bài 16).
- `Field(ge=0.0, le=2.0)`: ràng buộc giá trị, `LLM_TEMPERATURE=5` sẽ bị từ chối.
- Embedding mặc định là OpenAI vì Anthropic không có API embedding → cần `OPENAI_API_KEY` dù chat dùng Claude. Thiếu key, backend **dừng ngay lúc khởi động** (đã quan sát khi chạy trong Docker, xem bài 16).

## 3. `SecretStr` — secret không lọt vào log

```python
>>> settings.jwt_secret_key
SecretStr('**********')
>>> settings.jwt_secret_key.get_secret_value()
'dev-only-insecure-secret-change-me-in-production'
```

Nếu ai đó `logger.info(settings)` hay một traceback in ra object, secret vẫn bị che. Muốn giá trị thật phải gọi `.get_secret_value()` một cách **có chủ ý** — chỉ ở chỗ thật sự cần (ký JWT, tạo boto3 client).

## 4. Property suy ra từ cấu hình

```python
    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def checkpointer_conninfo(self) -> str:
        """LangGraph's Postgres checkpointer wants a plain libpq URL (no `+psycopg`)."""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
```

SQLAlchemy cần `postgresql+psycopg://...`, còn `psycopg_pool` (dùng cho checkpointer, xem [agent/10](../agent/10_MemoryPostgresCheckpointer.md)) cần URL libpq thuần. Một nguồn sự thật, hai định dạng → property thay vì hai biến môi trường dễ lệch nhau.

## 5. Validator: fail fast

```python
    @field_validator("jwt_secret_key")
    @classmethod
    def _check_secret_length(cls, value: SecretStr) -> SecretStr:
        # HS256 needs >= 256 bits of key material (RFC 7518 §3.2).
        if len(value.get_secret_value()) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters")
        return value

    @model_validator(mode="after")
    def _check_production_safety(self) -> Self:
        # Fail fast at startup instead of running production with dev defaults.
        if self.is_production:
            if self.jwt_secret_key.get_secret_value() == _DEV_JWT_SECRET:
                raise ValueError("JWT_SECRET_KEY must be set in production")
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true in production")
        return self
```

- `field_validator` kiểm một field; `model_validator(mode="after")` kiểm **tổ hợp** field (ở đây: `environment` + secret + cookie).
- Dev có secret mặc định để "clone về là chạy"; production thì **không thể** vô tình dùng nó — app không khởi động được. Test kiểm điều này trong `tests/test_unit.py::test_production_requires_real_secret`.

## 6. Cache settings

```python
@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (read env only once)."""
    return Settings()
```

Đọc `.env` một lần. Vì endpoint lấy settings qua `Depends(get_settings)`, test có thể thay bằng `app.dependency_overrides[get_settings] = lambda: settings` (bài 09, 15).

## 7. `.env.example` và Docker

```bash
# ---- PostgreSQL (docker compose -> localhost:5433; AWS RDS in production) ----
DATABASE_URL=postgresql+psycopg://app:app@localhost:5433/app

# ---- Auth ----
# >= 32 chars. Generate with: python -c "import secrets; print(secrets.token_urlsafe(64))"
# Leave commented out in development to use the built-in dev secret (refused in production).
# JWT_SECRET_KEY=
COOKIE_SECURE=false              # true in production (HTTPS only)

# ---- S3 (Floci locally; remove S3_ENDPOINT_URL to use real AWS) ----
S3_ENDPOINT_URL=http://localhost:4566
```

`.env` (bị `.gitignore`) chứa giá trị thật; `.env.example` (được commit) là tài liệu sống. Khi chạy trong Docker Compose, biến môi trường của service **ghi đè** `.env` — ví dụ `DATABASE_URL` trỏ sang host `postgres:5432` trong mạng Docker (bài 03, 16).

## ⚠️ Lỗi thường gặp

- `CORS_ORIGINS=http://localhost:3000` (không phải JSON) → lỗi parse. Phải là `["http://localhost:3000"]`.
- Đặt `JWT_SECRET_KEY=` (rỗng) thay vì comment dòng đó → validator báo "at least 32 characters".
- Quên rằng `get_settings()` được cache: sửa `.env` khi server đang chạy cần restart (`fastapi dev` tự reload khi sửa code, không phải khi sửa `.env`).
- Log `settings.model_dump()` ra console → `SecretStr` vẫn che, nhưng `database_url` chứa mật khẩu DB thì **không**. Ở production nên dùng secret manager và tránh log URL.

## 🧪 Thực hành

1. Chạy `ENVIRONMENT=production uv run python -c "from app.core.config import Settings; Settings()"` và đọc lỗi.
2. Thêm field `rate_limit_per_minute: int = Field(default=60, gt=0)` rồi thử `RATE_LIMIT_PER_MINUTE=0`.
3. Viết test cho `checkpointer_conninfo` với `database_url="postgresql+psycopg://u:p@h:1/db"`.

---

⬅️ Trước: [01 · Project Setup](01_ProjectSetup.md) · ➡️ Tiếp: [03 · Docker Compose: Postgres, Floci (S3), ChromaDB](03_DockerComposeInfra.md)
