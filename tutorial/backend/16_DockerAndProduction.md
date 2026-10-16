# 16 · Docker & Production

## 🎯 Mục tiêu

- Đọc hiểu `backend/Dockerfile`: **multi-stage**, cache layer dependency với `uv`, chạy **non-root**, `HEALTHCHECK`.
- Chạy toàn bộ hệ thống trong Docker Compose: migrate → backend → frontend, đúng thứ tự.
- Hiểu cấu hình mạng khi backend nằm **trong** Docker (hostname service, endpoint S3 công khai).
- Có một **checklist production** rõ ràng cho AWS (RDS, S3, secrets, scaling).

## 📁 File liên quan

- `backend/Dockerfile`, `backend/.dockerignore`
- `docker-compose.yml` (services `migrate`, `backend`, `frontend`)
- `Makefile` (`up`, `down`)
- `frontend/Dockerfile` → [frontend/15](../frontend/15_DockerAndDeployment.md)

---

## 1. Dockerfile — giai đoạn builder

```dockerfile
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.12 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Install dependencies first, in their own layer: it is only rebuilt when
# pyproject.toml / uv.lock change, not on every code edit.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY . .
```

- `COPY --from=ghcr.io/astral-sh/uv:0.12.12`: lấy đúng binary `uv` từ image chính thức, **ghim version** để build lặp lại được.
- `UV_COMPILE_BYTECODE=1`: biên dịch `.pyc` lúc build → container khởi động nhanh hơn. `UV_LINK_MODE=copy`: copy file thay vì hardlink (không cùng filesystem với cache). `UV_PYTHON_DOWNLOADS=0`: dùng Python của image, không tải bản khác.
- **Thứ tự layer** là mấu chốt: copy `pyproject.toml` + `uv.lock` → cài dependency → *rồi mới* copy code. Sửa một dòng code chỉ build lại layer cuối (vài giây), không cài lại LangChain, boto3… (vài phút).
- `--locked`: lỗi nếu `uv.lock` lệch với `pyproject.toml` — production cài **đúng** những gì đã test. `--no-dev`: không có pytest/ruff/mypy trong image.

> Tối ưu thêm (cần BuildKit/buildx): `RUN --mount=type=cache,target=/root/.cache/uv uv sync ...` giữ cache tải package giữa các lần build. Project không dùng vì Docker trên máy viết tutorial không có buildx (`the --mount option requires BuildKit`); nếu máy bạn có, nên thêm.

## 2. Dockerfile — giai đoạn runtime

```dockerfile
FROM python:3.12-slim

# Never run as root inside the container.
RUN useradd --create-home --uid 1000 app
WORKDIR /app
COPY --from=builder --chown=app:app /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
USER app

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health')"

CMD ["fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
```

- **Multi-stage**: image cuối không chứa `uv` hay cache build, chỉ có `.venv` + code.
- **Non-root** (`USER app`): nếu có lỗ hổng RCE, kẻ tấn công không có quyền root trong container.
- `PATH` trỏ vào `.venv/bin` → gọi thẳng `fastapi`, `alembic` (job `migrate` dùng chính image này).
- `PYTHONUNBUFFERED=1`: log ra stdout ngay, không bị giữ trong buffer — quan trọng cho `docker logs`/CloudWatch.
- `HEALTHCHECK` gọi **liveness** `/api/v1/health` (bài 14) bằng `urllib` — image slim không có `curl`.
- `--proxy-headers`: tin header `X-Forwarded-For/Proto` từ load balancer (để biết request gốc là HTTPS, IP thật của client). Uvicorn mặc định chỉ tin các header này khi đến từ `127.0.0.1`; sau ALB cần đặt thêm `FORWARDED_ALLOW_IPS` (dải IP của load balancer).

```
# .dockerignore
.venv
.env
**/__pycache__
.mypy_cache
.pytest_cache
.ruff_cache
tests
```

`.venv` của máy bạn (macOS) không chạy được trong Linux; `.env` chứa secret, **không bao giờ** được nằm trong image.

## 3. Compose: chạy cả hệ thống

```yaml
  migrate:
    profiles: ["app"]
    build: ./backend
    command: ["alembic", "upgrade", "head"]
    environment: &backend-env
      # Inside the compose network, services are reached by their service name.
      DATABASE_URL: postgresql+psycopg://app:app@postgres:5432/app
      S3_ENDPOINT_URL: http://floci:4566
      S3_PUBLIC_ENDPOINT_URL: http://localhost:4566 # presigned URLs are opened by the browser
      CHROMA_HOST: chroma
      CHROMA_PORT: "8000"

  backend:
    profiles: ["app"]
    build: ./backend
    env_file:
      - path: ./backend/.env # API keys etc.; optional
        required: false
    environment: *backend-env
    depends_on:
      migrate:
        condition: service_completed_successfully
      floci:
        condition: service_healthy
      chroma:
        condition: service_started
```

- **Hostname trong mạng Docker**: `postgres:5432`, không phải `localhost:5433` (bài 03). `S3_PUBLIC_ENDPOINT_URL` vẫn là `localhost` vì presigned URL do trình duyệt mở (bài 12).
- `&backend-env` / `*backend-env`: YAML anchor — khai báo một lần, dùng cho cả `migrate` và `backend`.
- `env_file` (`required: false`) mang `OPENAI_API_KEY` từ `backend/.env`; `environment` **ưu tiên hơn** `env_file`, nên `DATABASE_URL=...localhost:5433` trong `.env` bị ghi đè đúng ý.
- Thứ tự khởi động: `postgres` healthy → `migrate` chạy xong (exit 0) → `backend` khởi động → `backend` healthy → `frontend` ([frontend/15](../frontend/15_DockerAndDeployment.md)).

```bash
make up     # docker compose --profile app up -d --build --wait
make down   # docker compose --profile app down
```

### Những gì quan sát được khi chạy thử

- Không có `OPENAI_API_KEY`: container backend **thoát (exit 3)** ngay khi khởi động với `openai.OpenAIError: Missing credentials` — đúng tinh thần *fail fast* (bài 04). `docker compose logs backend` cho thấy ngay nguyên nhân.
- Với một key giả: stack healthy, `/health/ready` trả `ok`, register 201 / login 200 / me 200, request có `Origin` lạ → 403, upload → document chuyển sang `failed` với lỗi `401 Incorrect API key` (luồng thất bại hoạt động đúng), presigned download tải được file.

## 4. Checklist production (AWS)

**Bảo mật & cấu hình**

- [ ] `ENVIRONMENT=production` (ẩn `/docs`, bật validator).
- [ ] `JWT_SECRET_KEY` mạnh (`openssl rand -hex 32`), lấy từ **Secrets Manager / SSM Parameter Store**, không nằm trong image hay git.
- [ ] HTTPS ở load balancer + `COOKIE_SECURE=true`.
- [ ] `CORS_ORIGINS` = đúng domain frontend (cũng là danh sách cho kiểm tra CSRF).
- [ ] **Rate limiting** cho `/auth/login`, `/auth/register` (WAF hoặc middleware).
- [ ] Giới hạn kích thước body ở ALB/reverse proxy, khớp `MAX_UPLOAD_MB`.

**Dữ liệu**

- [ ] **RDS PostgreSQL**: Multi-AZ, automated backups + snapshot, encryption at rest, security group chỉ cho app truy cập.
- [ ] **S3**: bucket private, block public access, encryption, versioning/lifecycle; bỏ `S3_ENDPOINT_URL` và `AWS_ACCESS_KEY_ID/SECRET` → dùng **IAM role** của task (chỉ `GetObject/PutObject/DeleteObject` trên bucket đó).
- [ ] **Chroma**: server riêng có volume bền (hoặc Chroma Cloud), có backup; reset/khôi phục cùng lúc với Postgres để không lệch dữ liệu.
- [ ] Migration là **một bước deploy riêng** chạy trước rollout (bài 08).

**Vận hành & scale**

- [ ] Kết nối DB: `replicas × workers × (pool_size + max_overflow + pool checkpointer) < max_connections` của RDS (bài 04). Cân nhắc RDS Proxy.
- [ ] Thay `BackgroundTasks` bằng **hàng đợi thật** (SQS + worker, Celery, arq…): hiện ingestion chạy trong process API, mất khi container restart giữa chừng (document kẹt ở `processing`).
- [ ] Load balancer: health check → `/api/v1/health/ready`; idle timeout > 15 s và không buffer cho SSE (bài 13).
- [ ] Observability: log JSON có request id, metrics (latency, lỗi 5xx), tracing LLM (LangSmith…), cảnh báo khi document `failed` tăng.
- [ ] Image scan (Trivy, ECR scanning), cập nhật base image định kỳ.

## ⚠️ Lỗi thường gặp

- **`connection refused` tới `localhost:5433` từ container** → trong Docker phải dùng `postgres:5432`.
- **Copy code trước khi cài dependency** → mỗi lần sửa code là cài lại toàn bộ package.
- **`uv.lock` không được commit** → `--locked` fail, hoặc tệ hơn, mỗi build ra version khác nhau.
- **Chạy migration trong lifespan của mỗi replica** → race giữa các replica.
- **Backend `unhealthy` mãi** → xem `docker compose logs backend`; thường là thiếu `OPENAI_API_KEY` hoặc không kết nối được Chroma.

## 🧪 Thực hành

1. `make up`, rồi `docker compose ps` và `docker inspect --format '{{json .State.Health}}' <container-backend>`.
2. Sửa một dòng trong `app/main.py`, chạy `docker compose --profile app build backend` và quan sát layer `uv sync` được lấy từ cache (`CACHED`).
3. `docker compose exec backend whoami` → phải là `app`, không phải `root`.
4. Chọn một mục trong checklist (ví dụ đọc `JWT_SECRET_KEY` từ SSM khi khởi động) và phác thảo cách triển khai.

---

⬅️ Trước: [15 · Testing](15_Testing.md) · ➡️ Tiếp: [Frontend 01 · Project Setup](../frontend/01_ProjectSetup.md)
