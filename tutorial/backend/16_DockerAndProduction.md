# 16 · Docker & Production

## 🎯 Mục tiêu

- Nắm các khái niệm Docker nền tảng: image, container, Dockerfile, layer & cache.
- Đọc hiểu **từng dòng** của `backend/Dockerfile`: **multi-stage**, cache layer dependency với `uv`, chạy **non-root**, `HEALTHCHECK`, `CMD`.
- Chạy toàn bộ hệ thống trong Docker Compose: migrate → backend → frontend, đúng thứ tự.
- Hiểu cấu hình mạng khi backend nằm **trong** Docker (hostname service, endpoint S3 công khai).
- Có một **checklist production** rõ ràng cho AWS (RDS, S3, secrets, scaling).

## 📁 File liên quan

- `backend/Dockerfile`, `backend/.dockerignore`
- `docker-compose.yml` (services `migrate`, `backend`, `frontend`)
- `Makefile` (`up`, `down`)
- `frontend/Dockerfile` → [frontend/15](../frontend/15_DockerAndDeployment.md)

---

## 0. Docker cơ bản

| Khái niệm | Hiểu đơn giản |
| --- | --- |
| **Image** | Một "ổ đĩa đóng băng": hệ điều hành tối giản + Python + thư viện + code, đóng gói thành một khối không đổi. |
| **Container** | Một process đang chạy từ image, như một máy tính nhỏ cô lập. Một image chạy được nhiều container. |
| **Dockerfile** | Công thức tạo image. Docker đọc từ trên xuống, mỗi lệnh là một bước. |
| **Layer & cache** | Mỗi lệnh `COPY`/`RUN` tạo một **layer** chồng lên layer trước. Khi build lại, bước nào không đổi (cả input lẫn mọi bước trước nó) thì Docker dùng lại kết quả cũ (`CACHED`). Một bước thay đổi → **mọi bước sau nó** chạy lại. |

`backend/Dockerfile` dùng **multi-stage build**: hai giai đoạn, mỗi giai đoạn bắt đầu bằng một `FROM`.

```
┌─ Stage 1: builder ──────────────┐      ┌─ Stage 2: runtime (image cuối) ─┐
│ Python + uv                     │      │ Python (sạch)                   │
│ cài thư viện → /app/.venv       │ ───► │ chỉ copy /app (code + .venv)    │
│ copy code → /app                │      │ user không phải root            │
└─────────────────────────────────┘      └─────────────────────────────────┘
      bị bỏ đi sau khi build                    thứ được deploy
```

Stage 1 dùng để "nấu", stage 2 chỉ nhận "món ăn". Dụng cụ nấu (`uv`, cache tải package) không đi theo vào image cuối.

## 1. Dockerfile — giai đoạn builder

```dockerfile
FROM python:3.12-slim AS builder
```

- `FROM`: chọn **base image** làm điểm xuất phát, thay vì tự cài Linux + Python từ đầu. Image được tải từ Docker Hub.
- `python:3.12-slim`: Debian tối giản + Python 3.12 (~150 MB). Các biến thể khác:
  - `python:3.12` (bản đầy đủ, ~1 GB): kèm compiler và nhiều công cụ không cần thiết.
  - `python:3.12-alpine`: nhỏ hơn nữa nhưng dùng thư viện C `musl` thay `glibc`. Nhiều package Python (numpy, driver DB…) không có bản build sẵn cho musl, phải compile → build chậm, dễ lỗi.
- `AS builder`: đặt tên stage để stage sau lấy file từ nó.

```dockerfile
COPY --from=ghcr.io/astral-sh/uv:0.12.12 /uv /bin/uv
```

- `COPY` bình thường chép file từ máy bạn vào image. `--from=<image>` chép file **từ một image khác**.
- Lấy đúng binary `uv` từ image chính thức thay vì `pip install uv`: nhanh, gọn.
- **Ghim version** `0.12.12` → mọi lần build dùng cùng một bản uv, build lặp lại được (reproducible).

```dockerfile
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
```

`ENV` đặt biến môi trường cho các bước sau (`\` chỉ để xuống dòng). Ba biến cấu hình `uv`:

- `UV_COMPILE_BYTECODE=1`: biên dịch `.py` → `.pyc` lúc build → container khởi động nhanh hơn.
- `UV_LINK_MODE=copy`: mặc định uv *hardlink* file từ cache sang `.venv`. Trong Docker, cache và `.venv` thường khác filesystem nên không link được (uv cảnh báo) → copy thẳng.
- `UV_PYTHON_DOWNLOADS=0`: cấm uv tự tải Python khác, dùng đúng Python 3.12 của image.

```dockerfile
WORKDIR /app
```

Tương đương `mkdir -p /app && cd /app`: mọi lệnh sau chạy trong `/app`.

```dockerfile
# Install dependencies first, in their own layer: it is only rebuilt when
# pyproject.toml / uv.lock change, not on every code edit.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
```

**Thứ tự layer** là mấu chốt của cả file. Bước đầu **chỉ** copy hai file khai báo thư viện, chưa copy code; `RUN` chạy một lệnh **lúc build**, ở đây là cài thư viện vào `/app/.venv`.

Code thay đổi hằng ngày, danh sách thư viện thì hiếm khi. Sửa một dòng code rồi build lại:

```
COPY pyproject.toml uv.lock   → CACHED (2 file không đổi)
RUN uv sync ...               → CACHED (bỏ qua cài LangChain, boto3… mất vài phút)
COPY . .                      → chạy lại (code đổi) — vài giây
```

Nếu `COPY . .` đứng trước, mỗi lần sửa code là cài lại toàn bộ thư viện.

- `--locked`: cài **đúng** version trong `uv.lock`; lỗi ngay nếu `uv.lock` lệch với `pyproject.toml` — production chạy đúng những gì đã test.
- `--no-dev`: không có pytest/ruff/mypy trong image.
- `--no-install-project`: chỉ cài dependency, không cài chính project. Project đặt `package = false` nên vốn không có gì để cài, nhưng đây là pattern chuẩn của uv cho Docker.

> Tối ưu thêm (cần BuildKit/buildx): `RUN --mount=type=cache,target=/root/.cache/uv uv sync ...` giữ cache tải package giữa các lần build. Project không dùng vì Docker trên máy viết tutorial không có buildx (`the --mount option requires BuildKit`); nếu máy bạn có, nên thêm.

```dockerfile
COPY . .
```

Copy cả thư mục `backend/` (`.` thứ nhất, gọi là *build context*) vào `/app` (`.` thứ hai). `backend/.dockerignore` loại trừ:

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

- `.venv`: `.venv` trên máy bạn (macOS) chứa thư viện build cho macOS; nếu copy vào sẽ **ghi đè** `.venv` Linux vừa cài ở bước trên.
- `.env`: chứa secret, **không bao giờ** được nằm trong image.
- `tests`, các thư mục cache: production không cần.

## 2. Dockerfile — giai đoạn runtime

```dockerfile
FROM python:3.12-slim
```

`FROM` lần hai bắt đầu một image **mới tinh**. Mọi thứ của `builder` bị bỏ lại, trừ những gì ta chủ động copy sang → image cuối không chứa `uv` hay cache build. (Với project này lợi ích dung lượng còn khiêm tốn; nó lớn thấy rõ khi phải cài `gcc` để compile thư viện — compiler chỉ nằm ở builder.)

```dockerfile
# Never run as root inside the container.
RUN useradd --create-home --uid 1000 app
```

Mặc định process trong container chạy bằng **root**. Nếu app có lỗ hổng RCE, kẻ tấn công có quyền root trong container. Dòng này tạo user thường `app`.

- `--uid 1000`: kernel Linux nhận diện user bằng **số** (UID), không phải tên; tên `app` chỉ là một dòng trong `/etc/passwd` của image. Ghim UID để nó **cố định và biết trước** (bỏ đi thì `useradd` chọn UID trống đầu tiên — hiện tình cờ là 1000, nhưng base image đổi thì có thể thành 1001). Biết trước UID cần cho:
  - **Volume/bind mount**: file container ghi ra host thuộc UID 1000 — trên Linux thường chính là user của bạn, không phải `sudo chown`.
  - **Kubernetes** `runAsNonRoot: true`: kubelet chỉ kiểm tra được UID dạng số. Với `USER app` (tên) phải khai báo thêm `runAsUser: 1000`, hoặc đổi Dockerfile thành `USER 1000`.
  - **Storage dùng chung** như EFS access point (`posix_user { uid = 1000 }`).
  - Trade-off: UID 1000 trùng user thật đầu tiên trên host, nên nếu container bị escape, kẻ tấn công có quyền của user đó. Nhiều hướng dẫn bảo mật dùng UID cao (`10001`) để tránh trùng.
- `--create-home`: tạo `/home/app`. Nhiều thư viện ghi cache vào `$HOME` (ví dụ `~/.cache` của Hugging Face nếu chạy reranker local, [agent/13](../agent/13_NextSteps.md) §4.1).

```dockerfile
WORKDIR /app
COPY --from=builder --chown=app:app /app /app
```

- `--from=builder`: copy từ **stage builder** (tên ở `AS builder`), không phải từ máy bạn — nguyên `/app` gồm code + `.venv` đã cài.
- `--chown=app:app`: đổi chủ sở hữu sang user `app` (mặc định file copy vào thuộc root).

```dockerfile
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
```

- `PATH`: đặt `.venv/bin` lên đầu → `python`, `fastapi`, `alembic` gọi bản trong `.venv`. Tương đương `source .venv/bin/activate`.
- `PYTHONUNBUFFERED=1`: Python mặc định gom output vào buffer rồi mới in; trong container điều đó làm log hiện trễ hoặc **mất khi crash**. Biến này bắt in ngay — quan trọng cho `docker logs`/CloudWatch.

```dockerfile
USER app
```

Từ đây trở đi, và **khi container chạy**, process chạy bằng `app` thay vì root.

```dockerfile
EXPOSE 8000
```

**Chỉ là tài liệu**: cho biết app lắng nghe cổng 8000. Nó **không** mở cổng ra ngoài; việc đó do `docker run -p 8000:8000` hoặc `ports:` trong `docker-compose.yml` làm.

```dockerfile
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health')"
```

Docker tự chạy lệnh này **bên trong container** mỗi 10 s (`--interval`), mỗi lần chờ tối đa 3 s (`--timeout`); fail 5 lần liên tiếp (`--retries`) → container bị đánh dấu `unhealthy`.

- Gọi **liveness** `/api/v1/health`, không phải readiness (bài [14](14_ErrorHandlingAndHealthChecks.md)): Chroma chậm không làm container bị coi là hỏng.
- Dùng `urllib` của Python vì image slim **không có `curl`**; cài curl chỉ để healthcheck thì phí dung lượng.
- Compose dựa vào kết quả này: `frontend` chờ `backend` có `condition: service_healthy` (mục 3).
- ECS và Kubernetes **bỏ qua** `HEALTHCHECK` trong Dockerfile; chúng có cấu hình health check riêng (container health check, `livenessProbe`).

```dockerfile
# `fastapi run` = production uvicorn (no reload). --proxy-headers: trust X-Forwarded-*
# from the load balancer in front of us.
CMD ["fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
```

`CMD` là **lệnh mặc định khi container khởi động**. Khác `RUN`: `RUN` chạy **lúc build**, `CMD` chạy **lúc start**.

- **Dạng mảng JSON** (*exec form*) thay vì chuỗi `fastapi run ...` (*shell form*): `fastapi` là process chính (PID 1) nên **nhận trực tiếp SIGTERM** khi Docker dừng container → kịp đóng kết nối DB, kết thúc stream (graceful shutdown). Với shell form, PID 1 là `/bin/sh`, tín hiệu không tới app; Docker chờ 10 s rồi kill cứng.
- `fastapi run`: uvicorn chế độ production, không auto-reload như `fastapi dev`.
- `--host 0.0.0.0`: lắng nghe trên **mọi** network interface. Với `127.0.0.1`, app chỉ nhận kết nối từ bên trong chính container → port mapping từ máy bạn không vào được.
- `--proxy-headers`: tin header `X-Forwarded-For/Proto` từ load balancer (để biết request gốc là HTTPS, IP thật của client). Uvicorn mặc định chỉ tin các header này khi đến từ `127.0.0.1`; sau ALB cần đặt thêm `FORWARDED_ALLOW_IPS` (dải IP của load balancer).
- `CMD` chỉ là **mặc định**: service `migrate` (mục 3) dùng **chính image này** nhưng ghi đè bằng `command: ["alembic", "upgrade", "head"]`. Một image, hai cách dùng.

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
- **Thiếu `.venv` trong `.dockerignore`** → `.venv` macOS ghi đè `.venv` Linux trong image, container lỗi khi import thư viện có phần native.
- **`--host 127.0.0.1` trong container** → `curl localhost:8000` từ máy bạn bị `connection reset`/`refused` dù container vẫn chạy.
- **`CMD` dạng chuỗi (shell form)** → app không nhận SIGTERM, `docker stop` luôn mất 10 s và kết nối bị cắt ngang.
- **`uv.lock` không được commit** → `--locked` fail, hoặc tệ hơn, mỗi build ra version khác nhau.
- **Chạy migration trong lifespan của mỗi replica** → race giữa các replica.
- **Backend `unhealthy` mãi** → xem `docker compose logs backend`; thường là thiếu `OPENAI_API_KEY` hoặc không kết nối được Chroma.

## 🧪 Thực hành

1. Build và soi image backend:
   ```bash
   cd backend
   docker build -t rag-backend .
   docker images rag-backend               # dung lượng image
   docker history rag-backend              # từng layer và kích thước
   docker run --rm rag-backend which python  # → /app/.venv/bin/python (nhờ PATH)
   ```
2. `make up`, rồi `docker compose ps` và `docker inspect --format '{{json .State.Health}}' <container-backend>`.
3. Sửa một dòng trong `app/main.py`, chạy `docker compose --profile app build backend` và quan sát layer `uv sync` được lấy từ cache (`CACHED`).
4. `docker compose exec backend whoami` → phải là `app`, không phải `root`.
5. Chọn một mục trong checklist (ví dụ đọc `JWT_SECRET_KEY` từ SSM khi khởi động) và phác thảo cách triển khai.

---

⬅️ Trước: [15 · Testing](15_Testing.md) · ➡️ Tiếp: [Frontend 01 · Project Setup](../frontend/01_ProjectSetup.md)
