# 03 · Docker Compose: Postgres, Floci (S3), ChromaDB

## 🎯 Mục tiêu

- Dựng toàn bộ hạ tầng local bằng **một lệnh**: PostgreSQL, S3 giả lập (Floci), ChromaDB.
- Hiểu từng service tương ứng với dịch vụ nào trên cloud (RDS, S3…).
- Đọc hiểu `ports`, `volumes`, `healthcheck`, `profiles`, `depends_on`.
- Biết cách kiểm tra từng service bằng tay.

## 📁 File liên quan

- `docker-compose.yml` (root)
- `Makefile` (`infra-up`, `infra-down`, `infra-logs`, `up`, `down`)
- `backend/app/core/config.py` (các cổng mặc định khớp với compose)

---

## 1. Bức tranh tổng thể

```
           máy của bạn (host)                     mạng Docker "ai-agent-fullstack_default"
┌────────────────────────────────┐   ┌──────────────────────────────────────────────┐
│ uv run fastapi dev  (:8000)    │──►│ localhost:5433 → postgres:5432  (RDS)        │
│ npm run dev         (:3000)    │──►│ localhost:4566 → floci:4566     (S3)         │
│                                │──►│ localhost:8001 → chroma:8000    (ChromaDB)   │
└────────────────────────────────┘   └──────────────────────────────────────────────┘
```

| Service | Image | Production tương ứng | Lưu gì |
| --- | --- | --- | --- |
| `postgres` | `postgres:17-alpine` | AWS RDS for PostgreSQL | users, refresh_tokens, documents, conversations, checkpoint LangGraph |
| `floci` | `floci/floci:2.3.0` | AWS S3 | file gốc người dùng tải lên |
| `chroma` | `chromadb/chroma:1.5.9` | Chroma self-host / Chroma Cloud | embedding của các chunk |

Nguyên tắc **dev/prod parity**: local dùng *cùng loại* dịch vụ với production (Postgres thật chứ không phải SQLite, API S3 thật chứ không phải thư mục trên đĩa). Code chỉ khác nhau ở **cấu hình** (URL, credentials) — bài 02.

## 2. PostgreSQL — thay cho RDS

```yaml
  postgres:
    image: postgres:17-alpine
    environment:
      POSTGRES_USER: app
      POSTGRES_PASSWORD: app
      POSTGRES_DB: app
    ports:
      - "5433:5432" # 5433 on the host to avoid clashing with a local Postgres
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app -d app"]
      interval: 5s
      timeout: 3s
      retries: 10
```

- `ports: "5433:5432"` = `HOST:CONTAINER`. Trong container Postgres vẫn nghe 5432; trên máy bạn nó là **5433**, vì nhiều máy dev đã có sẵn Postgres ở 5432 (máy viết tutorial này cũng vậy). Do đó `DATABASE_URL` mặc định là `...@localhost:5433/app`.
- `volumes`: dữ liệu nằm trong *named volume* `postgres-data` → `docker compose down` rồi `up` lại vẫn còn. Muốn xoá sạch: `docker compose down -v`.
- `healthcheck`: `pg_isready` trả 0 khi Postgres nhận kết nối. Compose dùng thông tin này cho `--wait` và `depends_on: condition: service_healthy`.
- `-alpine`: image nhỏ hơn nhiều so với bản Debian.

## 3. Floci — S3 chạy local, miễn phí

[Floci](https://floci.io/aws/) là một trình giả lập AWS chạy local (mã nguồn: [github.com/kurtmc/floci](https://github.com/kurtmc/floci)), nói **đúng API của AWS** ở cổng 4566 — nên boto3 dùng y như với AWS thật, chỉ khác `endpoint_url`. Project chỉ dùng S3; S3 hoạt động mà **không cần** mount Docker socket.

```yaml
  floci:
    image: floci/floci:2.3.0
    environment:
      FLOCI_STORAGE_MODE: persistent
    ports:
      - "4566:4566"
    volumes:
      - floci-data:/app/data
    healthcheck:
      # The image has no curl/wget: open a TCP connection with bash instead.
      test: ["CMD", "bash", "-c", "exec 3<>/dev/tcp/127.0.0.1/4566"]
```

- `FLOCI_STORAGE_MODE: persistent` + volume `/app/data`: object không mất khi restart container.
- **Healthcheck không có curl**: image Floci không cài `curl`/`wget`, nên ta dùng tính năng có sẵn của bash: mở file đặc biệt `/dev/tcp/HOST/PORT` = mở kết nối TCP. Kết nối được → exit 0 → healthy. Đây là mẹo hữu ích cho mọi image tối giản có bash.
- Floci còn có endpoint `/_floci/health`, nhưng gọi nó lại cần một HTTP client — mà image không có.
- Credentials: Floci chấp nhận bất kỳ access key nào; project dùng `test`/`test` (bài 02).

> Ở production, bỏ `S3_ENDPOINT_URL` (để `None`) và boto3 sẽ nói chuyện với AWS S3 thật.

## 4. ChromaDB — vector store

```yaml
  chroma:
    image: chromadb/chroma:1.5.9
    environment:
      IS_PERSISTENT: "TRUE"
      ANONYMIZED_TELEMETRY: "FALSE"
    ports:
      - "8001:8000" # backend uses 8000 on the host
    volumes:
      - chroma-data:/data
```

- Chroma nghe 8000 trong container, nhưng cổng 8000 của host đã dành cho FastAPI → map ra **8001**.
- Chroma server dùng phiên bản **khớp** với thư viện client `chromadb` trong `pyproject.toml` (1.5.9). Lệch phiên bản client/server là nguồn lỗi khó hiểu phổ biến.
- Cách backend dùng Chroma (collection, filter theo `user_id`): [agent/04](../agent/04_EmbeddingsAndVectorStore.md).

## 5. Profiles: hạ tầng vs. ứng dụng

```yaml
  migrate:
    profiles: ["app"]
    ...
  backend:
    profiles: ["app"]
    ...
  frontend:
    profiles: ["app"]
```

Service có `profiles` **không** chạy với `docker compose up` thường. Nhờ vậy cùng một file phục vụ hai cách làm việc:

| Lệnh | Chạy gì | Khi nào |
| --- | --- | --- |
| `make infra-up` | postgres, floci, chroma | Hằng ngày: backend/frontend chạy bằng `uv`/`npm` để có hot-reload |
| `make up` | + migrate, backend, frontend (build image) | Thử toàn bộ hệ thống như production |

Chi tiết `migrate`, `backend`, `frontend` ở bài 16 và [frontend/15](../frontend/15_DockerAndDeployment.md).

## 6. Makefile

```makefile
infra-up: ## Start Postgres, Floci (S3) and ChromaDB in Docker
	docker compose up -d --wait postgres floci chroma

infra-down: ## Stop the infrastructure (data is kept in Docker volumes)
	docker compose down
```

`--wait` chờ tới khi mọi service **healthy** (hoặc running nếu không có healthcheck) rồi mới trả về → lệnh `make migrate` chạy ngay sau đó không bị "connection refused".

## 7. Kiểm tra bằng tay

```bash
docker compose ps                                     # trạng thái + (healthy)
docker compose exec postgres psql -U app -d app -c '\dt'   # liệt kê bảng
curl -s localhost:8001/api/v2/heartbeat                # Chroma
aws --endpoint-url http://localhost:4566 s3 ls        # Floci (cần AWS CLI, key bất kỳ)
```

Khi backend đã chạy, `GET /api/v1/health/ready` kiểm tra cả ba cùng lúc (bài 14):

```json
{"status":"ok","checks":{"database":true,"storage":true,"vector_store":true}}
```

## ⚠️ Lỗi thường gặp

- **`port is already allocated`**: cổng host đang bị chiếm. Đổi vế trái của `ports` (và biến tương ứng trong `.env`), không đổi vế phải.
- **Dùng `localhost` bên trong container**: trong mạng Docker, `localhost` là chính container đó. Container backend phải gọi `postgres:5432`, `floci:4566`, `chroma:8000` (bài 16).
- **`docker compose down -v`** xoá volume = mất hết dữ liệu (user, tài liệu, vector). Chỉ dùng khi muốn reset.
- **Lệch dữ liệu giữa các store**: xoá volume `chroma-data` nhưng giữ Postgres → tài liệu "ready" nhưng tìm không ra chunk nào. Reset thì reset cả ba.

## 🧪 Thực hành

1. `make infra-up`, rồi `docker compose stop chroma` và gọi `/api/v1/health/ready`: quan sát `503` và `"vector_store": false`.
2. Tải lên một tài liệu (sau bài 12), rồi liệt kê object bằng `aws --endpoint-url http://localhost:4566 s3 ls s3://documents --recursive`.
3. Thêm service `adminer` (image `adminer`, cổng `8080:8080`) có `profiles: ["tools"]` để xem DB qua trình duyệt, chạy bằng `docker compose --profile tools up -d`.

---

⬅️ Trước: [02 · Configuration](02_Configuration.md) · ➡️ Tiếp: [04 · App Factory & Lifespan](04_AppFactoryAndLifespan.md)
