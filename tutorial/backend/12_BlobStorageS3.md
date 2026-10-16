# 12 · Blob Storage với S3 (Floci local, AWS S3 production)

## 🎯 Mục tiêu

- Hiểu vì sao file gốc nằm ở **object storage** (S3), không nằm trong Postgres hay trên đĩa server.
- Cấu hình boto3 chạy được với **cả** Floci và AWS S3 thật, chỉ khác biến môi trường.
- Gọi boto3 (thư viện **đồng bộ**) từ code async mà không chặn event loop.
- Dùng **presigned URL** để trình duyệt tải file trực tiếp từ S3.
- Xử lý bài toán "hai hostname" khi chạy trong Docker.

## 📁 File liên quan

- `backend/app/storage/s3.py`
- `backend/app/core/config.py` (`s3_*`, `aws_*`)
- `backend/app/services/document_service.py` (người dùng `S3Storage`)
- `backend/tests/conftest.py` (moto)

---

## 1. Vì sao S3?

| Cách lưu | Vấn đề |
| --- | --- |
| Cột `BYTEA` trong Postgres | DB phình to, backup chậm, RDS tính tiền theo GB đắt hơn S3 nhiều |
| Thư mục trên đĩa server | Mất khi container bị thay; nhiều replica không thấy file của nhau |
| **S3** | Rẻ, bền (11 số 9), mọi replica dùng chung, có presigned URL, lifecycle, versioning |

Postgres chỉ giữ metadata + `s3_key` (bài 07). Local dùng **Floci** nói đúng API S3 ở `localhost:4566` (bài 03).

## 2. Một client cho mọi môi trường

```python
def _make_client(settings: Settings, endpoint_url: str | None) -> BaseClient:
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=settings.aws_region,
        # None -> boto3's default chain (env vars, IAM role, ...): what you want on AWS.
        aws_access_key_id=(
            settings.aws_access_key_id.get_secret_value() if settings.aws_access_key_id else None
        ),
        ...
        # Path-style URLs (http://host/bucket/key) work with local emulators.
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
```

| Biến | Local (Floci) | Production (AWS) |
| --- | --- | --- |
| `S3_ENDPOINT_URL` | `http://localhost:4566` | *(để trống → `None`)* |
| `AWS_ACCESS_KEY_ID` / `SECRET` | `test` / `test` | *(để trống → IAM role của ECS task/EC2)* |
| `S3_BUCKET` | `documents` | tên bucket thật |

- **Credentials `None`** → boto3 tự tìm theo *default credential chain*: biến môi trường, `~/.aws`, IAM role. Trên AWS, IAM role là cách đúng: không có secret dài hạn nào nằm trong `.env`.
- **Path-style** (`http://host/bucket/key`) thay vì virtual-hosted (`http://bucket.host/key`): emulator local chỉ có một hostname. AWS vẫn hỗ trợ path-style cho bucket hiện có, nhưng khuyến nghị virtual-hosted — có thể đổi thành cấu hình nếu cần.
- `signature_version="s3v4"`: bắt buộc cho presigned URL ở mọi region mới.

## 3. boto3 đồng bộ trong app async

```python
"""boto3 is synchronous, so every call runs in a worker thread via `asyncio.to_thread`
to keep the event loop free.
"""

    async def upload(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
```

FastAPI chạy mọi request trên **một** event loop. Một lời gọi `put_object` mất 200 ms mà chạy thẳng trong `async def` → **mọi** request khác (kể cả SSE đang stream token) đứng hình 200 ms. `asyncio.to_thread` đẩy lời gọi sang thread pool và `await` kết quả.

Ruff rule `ASYNC` bắt được `time.sleep`, `open()`, `urllib` trong hàm async, nhưng **không** biết boto3 — kỷ luật này phải tự giữ (bài 01). Client boto3 thread-safe nên dùng chung giữa các thread được.

Lựa chọn khác: `aioboto3`/`aiobotocore` (async thật). Project giữ boto3 vì phổ biến, tài liệu nhiều, và `to_thread` đủ tốt cho tải vừa phải.

## 4. Bucket tự tạo khi khởi động

```python
    async def ensure_bucket(self) -> None:
        def _ensure() -> None:
            try:
                self._client.head_bucket(Bucket=self._bucket)
            except ClientError:
                logger.info("Creating bucket %s", self._bucket)
                self._client.create_bucket(Bucket=self._bucket)

        await asyncio.to_thread(_ensure)
```

Gọi trong lifespan (bài 04): Floci và moto khởi động rỗng. Ở production bucket nên được tạo bằng Terraform/CDK với policy, encryption, lifecycle; IAM role của app chỉ cần `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` — `head_bucket` thành công nên `create_bucket` không bao giờ chạy. Readiness probe cũng dùng hàm này (bài 14).

## 5. Presigned URL — file không đi qua API

```python
    async def presigned_download_url(self, key: str, filename: str, expires_in: int = 300) -> str:
        """A short-lived URL that lets the browser download the file directly from S3."""
        url: str = await asyncio.to_thread(
            self._public_client.generate_presigned_url,
            "get_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
            },
            ExpiresIn=expires_in,
        )
        return url
```

```
Trình duyệt ──GET /documents/{id}/download──► API: kiểm tra owner (bài 11) → ký URL (không gọi mạng)
Trình duyệt ──GET presigned URL────────────► S3: kiểm chữ ký + hạn → trả file
```

- API không phải stream file 50 MB qua mình → không tốn RAM/băng thông/worker.
- URL chứa chữ ký + thời hạn (5 phút, `PRESIGNED_URL_TTL_SECONDS`); ai có URL đều tải được trong thời gian đó → giữ ngắn.
- `ResponseContentDisposition: attachment; filename=...` → S3 trả header khiến trình duyệt **tải về** với tên gốc, thay vì tên là UUID trong key.
- Ký URL là phép tính cục bộ (HMAC), không gọi mạng; vẫn bọc `to_thread` cho đồng nhất.

Frontend mở URL bằng `window.open(url, "_blank", "noopener")` ([frontend/12](../frontend/12_DocumentsPage.md)). Đây là **điều hướng**, không phải `fetch`, nên không cần CORS. Nếu muốn `fetch()` URL đó (ví dụ để xem trước trong trang), trình duyệt sẽ chặn: *"blocked by CORS policy: No 'Access-Control-Allow-Origin' header"* — lúc đó phải cấu hình CORS cho bucket (`put_bucket_cors`).

## 6. Hai hostname: trong Docker vs. trình duyệt

```python
        self._client: Any = _make_client(settings, settings.s3_endpoint_url)
        # Presigned URLs are opened by the browser, which may need a different host.
        public_endpoint = settings.s3_public_endpoint_url or settings.s3_endpoint_url
        self._public_client: Any = _make_client(settings, public_endpoint)
```

Khi backend chạy trong Docker Compose (bài 16):

- Backend nói chuyện với S3 qua `http://floci:4566` (tên service trong mạng Docker).
- Nhưng URL ký bằng host `floci` thì trình duyệt trên máy bạn không phân giải được.

→ `S3_PUBLIC_ENDPOINT_URL=http://localhost:4566` cho riêng `_public_client`. Không thể ký bằng `floci` rồi thay chuỗi thành `localhost`: host là một phần của chữ ký, sửa là `SignatureDoesNotMatch`. Ở production cả hai đều để trống (AWS).

## 7. Test với moto

```python
@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        yield
```

`moto` chặn mọi lời gọi boto3 và giả lập S3 trong bộ nhớ. Settings test đặt `s3_endpoint_url=None` (để boto3 "gọi AWS", moto bắt lại) và bucket `test-documents`. Test không cần Docker (bài 15).

## ⚠️ Lỗi thường gặp

- **`NoSuchBucket`**: lifespan chưa chạy `ensure_bucket` (hoặc tên bucket khác giữa các service).
- **`SignatureDoesNotMatch` khi mở presigned URL**: sửa URL sau khi ký, lệch giờ hệ thống, hoặc ký bằng endpoint khác với endpoint trình duyệt gọi.
- **Gọi boto3 thẳng trong `async def`** → chat stream giật khi có người upload.
- **Bucket public** để "cho tiện" → ai đoán được key là tải được file người khác. Bucket luôn private; truy cập qua presigned URL.
- **Tên file có dấu ngoặc kép/ký tự Unicode** trong `filename="..."`: nên dùng thêm dạng `filename*=UTF-8''...` (RFC 6266/5987) để an toàn với mọi trình duyệt.

## 🧪 Thực hành

1. Upload một tài liệu, gọi `GET /api/v1/documents/{id}/download`, rồi `curl -o out.pdf "<url>"`. Đợi 5 phút và thử lại: `AccessDenied`/`Request has expired`.
2. `aws --endpoint-url http://localhost:4566 s3 ls s3://documents --recursive` và đối chiếu key với cột `s3_key`.
3. Thêm method `presigned_upload_url` (`put_object`) để trình duyệt upload thẳng lên S3; nghĩ xem backend cần biết gì khi upload xong để bắt đầu ingestion.

---

⬅️ Trước: [11 · Service Layer](11_ServiceLayer.md) · ➡️ Tiếp: [Agent 01 · LangChain Fundamentals](../agent/01_LangChainFundamentals.md)
