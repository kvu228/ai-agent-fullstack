# 10 · Authentication: Argon2, JWT, Refresh Token, Cookie & CSRF

## 🎯 Mục tiêu

- Hash mật khẩu đúng cách (Argon2id) và chống **dò email** qua thời gian phản hồi.
- Thiết kế hai loại token: **access token** (JWT ngắn hạn) + **refresh token** (ngẫu nhiên, lưu hash trong DB).
- **Xoay vòng** refresh token và **phát hiện tái sử dụng** (token bị đánh cắp).
- Giao token cho trình duyệt bằng **cookie httpOnly**, chặn **CSRF** bằng SameSite + kiểm tra `Origin`.
- Biết các giới hạn còn lại và việc nên làm thêm trước khi lên production.

## 📁 File liên quan

- `backend/app/core/security.py` — hash, JWT, refresh token (không có DB/HTTP)
- `backend/app/services/auth_service.py` — register, login, refresh, logout
- `backend/app/api/v1/endpoints/auth.py` — cookie
- `backend/app/api/deps.py` — `get_current_user`, `_check_csrf_origin`
- `backend/tests/test_auth.py`, `backend/tests/test_unit.py`

---

## 1. Bức tranh tổng thể

```
POST /auth/login ──► verify Argon2 ──► access JWT (15 phút) + refresh token (7 ngày)
                                          │                      │
                                  cookie access_token    cookie refresh_token
                                          │              (DB chỉ lưu SHA-256)
GET /documents ──► get_current_user: đọc JWT, kiểm chữ ký + exp ──► User
       │ 401 khi JWT hết hạn
POST /auth/refresh ──► revoke token cũ, cấp cặp mới (rotation)
```

| | Access token | Refresh token |
| --- | --- | --- |
| Dạng | JWT ký HS256 | 64 ký tự ngẫu nhiên (`token_urlsafe(48)`) |
| Sống | 15 phút (`ACCESS_TOKEN_TTL_MINUTES`) | 7 ngày (`REFRESH_TOKEN_TTL_DAYS`) |
| Kiểm tra | Chỉ kiểm chữ ký, **không** hỏi DB | Tra DB theo hash |
| Thu hồi | Không được (chờ hết hạn) | Được (`revoked_at`) |

Access token ngắn hạn nên việc không thu hồi được là chấp nhận được; refresh token dài hạn nên **phải** thu hồi được.

## 2. Mật khẩu

```python
# Argon2id with library-recommended parameters (the current OWASP recommendation).
_password_hash = PasswordHash.recommended()

# Hash of a random password: used to spend the same time when the user doesn't exist,
# so response timing doesn't reveal which emails are registered.
DUMMY_PASSWORD_HASH = _password_hash.hash(secrets.token_urlsafe(16))
```

- `pwdlib` + Argon2id: hàm hash **chậm có chủ đích**, có salt riêng mỗi mật khẩu. Không bao giờ dùng MD5/SHA-256 cho mật khẩu.

```python
    async def authenticate(self, email: str, password: str) -> User:
        user = await self._session.scalar(select(User).where(User.email == email))
        # Always run one hash verification, even for unknown emails (timing-safe).
        hashed = user.hashed_password if user else DUMMY_PASSWORD_HASH
        password_ok = verify_password(password, hashed)
        if user is None or not password_ok or not user.is_active:
            # Same message for every case: don't reveal which emails exist.
            raise AuthenticationError("Incorrect email or password")
        return user
```

Nếu email không tồn tại mà trả lời ngay (bỏ qua Argon2 ~50 ms), kẻ tấn công đo thời gian là biết email nào đã đăng ký. `DUMMY_PASSWORD_HASH` làm hai trường hợp tốn thời gian như nhau, và thông báo lỗi giống hệt nhau (test `test_login_wrong_password_and_unknown_email_look_the_same`).

Đăng ký dựa vào ràng buộc `UNIQUE` của DB: `IntegrityError` → `ConflictError` (409), xem bài 11.

## 3. Access token (JWT)

```python
    payload = {
        "sub": str(user_id),
        "type": "access",
        "iat": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
    }
```

```python
    claims: dict[str, Any] = jwt.decode(
        token,
        settings.jwt_secret_key.get_secret_value(),
        # Always pin the algorithm list: never trust the token's own "alg" header.
        algorithms=[settings.jwt_algorithm],
        options={"require": ["sub", "exp", "type"]},
    )
    if claims["type"] != "access":
        raise jwt.InvalidTokenError("Not an access token")
```

- **Ghim `algorithms`**: lỗ hổng kinh điển là token tự khai `"alg": "none"` hoặc đổi thuật toán. PyJWT bắt buộc truyền danh sách — đừng bao giờ lấy từ header token.
- `require`: thiếu `exp` → từ chối (token không hết hạn là thảm hoạ).
- `type`: phòng khi sau này có loại JWT khác (reset mật khẩu…) bị dùng nhầm làm access token.
- JWT chỉ được **ký**, không mã hoá: ai cũng decode base64 đọc được payload. Đừng đặt dữ liệu nhạy cảm vào.
- `JWT_SECRET_KEY` ≥ 32 ký tự (validator trong `Settings`, bài 02); ở production không được là giá trị mặc định.

## 4. Refresh token: lưu hash, xoay vòng, phát hiện tái sử dụng

```python
def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    # A fast hash is fine here: the token is 384 random bits, not a guessable password.
    return hashlib.sha256(token.encode()).hexdigest()
```

DB bị lộ → kẻ tấn công chỉ có hash, không dùng được. SHA-256 đủ vì không ai brute-force được 384 bit ngẫu nhiên (khác với mật khẩu người đặt).

```python
        if record.revoked_at is not None:
            logger.warning("Refresh token reuse detected for user %s", record.user_id)
            await self._revoke_all(record.user_id)
            raise AuthenticationError("Refresh token reuse detected; please log in again")

        if _as_utc(record.expires_at) <= datetime.now(UTC):
            raise AuthenticationError("Refresh token expired")
        ...
        record.revoked_at = datetime.now(UTC)
        return user, await self.issue_tokens(user)  # issue_tokens commits both changes
```

**Rotation**: mỗi lần refresh, token cũ bị revoke và cấp token mới. Nếu một token **đã revoke** lại xuất hiện, có hai người đang giữ nó — người dùng thật và kẻ trộm. Không biết ai là ai, nên thu hồi **mọi** phiên của user đó; cả hai phải đăng nhập lại. Test `test_refresh_rotates_and_detects_reuse` chứng minh: dùng lại token cũ → 401, rồi cả token mới cũng 401.

`_as_utc`: SQLite trong test trả datetime không có múi giờ, Postgres trả có — so sánh hai loại sẽ lỗi `TypeError`.

> ⚠️ Giới hạn: hai **tab** cùng refresh đúng lúc có thể gửi cùng một token → tab thứ hai bị coi là tái sử dụng → đăng xuất. Frontend gộp các lần refresh đồng thời **trong một tab** ([frontend/06](../frontend/06_ApiClientAndTokenRefresh.md)); giải pháp đầy đủ là cho phép một "grace period" vài giây.

## 5. Cookie cho trình duyệt

```python
        response.set_cookie(
            name,
            value,
            max_age=max_age,
            path="/",
            httponly=True,  # invisible to JavaScript -> an XSS bug can't steal the token
            secure=settings.cookie_secure,  # HTTPS only (required in production)
            samesite="lax",  # not sent on cross-site POSTs -> first line of CSRF defence
        )
```

- **httpOnly**: `document.cookie` không đọc được → lỗi XSS không lấy được token (so với `localStorage`).
- **Secure**: chỉ gửi qua HTTPS; `COOKIE_SECURE=false` chỉ dành cho `http://localhost`. Validator chặn `ENVIRONMENT=production` mà `COOKIE_SECURE=false`.
- **`path="/"` cho cả refresh cookie**: chặt hơn sẽ là `path="/api/v1/auth/refresh"`, nhưng khi đó `proxy.ts` của Next.js (chạy ở mọi route) không thấy cookie để biết người dùng còn phiên ([frontend/07](../frontend/07_AuthenticationFlow.md)). Đánh đổi có ý thức.
- `/auth/login` vẫn trả `access_token` trong JSON cho client không phải trình duyệt; refresh token thì không.

Refresh thất bại thì endpoint tự dựng response 401 và **xoá cookie**, nếu không trình duyệt sẽ cứ gửi cookie chết:

```python
    except AuthenticationError as exc:
        error = JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )
        _clear_auth_cookies(error)
        return error
```

## 6. CSRF

Cookie được trình duyệt **tự động** gắn vào request — kể cả request do trang `evil.example` kích hoạt. Hai lớp bảo vệ:

1. `SameSite=Lax`: trình duyệt không gửi cookie trong POST/DELETE cross-site.
2. Kiểm tra `Origin` cho request đổi trạng thái dùng cookie:

```python
def _check_csrf_origin(request: Request, settings: Settings) -> None:
    if request.method in SAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin is not None and origin not in settings.cors_origins:
        raise PermissionDeniedError("Cross-site request blocked")
```

- Chỉ áp dụng cho nhánh cookie trong `get_current_user` (bài 09). Request dùng `Authorization: Bearer` miễn nhiễm: trình duyệt không bao giờ tự thêm header đó.
- Không có `Origin` (curl, client server-side) → cho qua; ở đó không có "nạn nhân trình duyệt".
- Test: `test_cross_site_cookie_request_is_blocked` gửi `Origin: https://evil.example` → 403.

## ⚠️ Lỗi thường gặp

- **Lưu JWT trong `localStorage`** → XSS đọc được. Dùng cookie httpOnly.
- **`jwt.decode(token, key)` không có `algorithms`** → PyJWT báo lỗi; đừng "sửa" bằng cách lấy `alg` từ header.
- **Đổi `JWT_SECRET_KEY`** → mọi access token cũ không hợp lệ (người dùng chỉ cần refresh). Đó là cách "đăng xuất toàn bộ" khẩn cấp.
- **Login trên `http://` với `COOKIE_SECURE=true`** → trình duyệt bỏ cookie, login "thành công" nhưng `/auth/me` vẫn 401.
- **Thêm frontend ở origin mới mà quên `CORS_ORIGINS`** → mọi POST bị 403 "Cross-site request blocked".

## 🧪 Thực hành

1. Dán một access token vào [jwt.io](https://jwt.io) để thấy payload đọc được mà không cần khoá.
2. Chạy `uv run pytest tests/test_auth.py -v` và đọc từng test tương ứng với từng mục ở trên.
3. Thêm **rate limiting** cho `/auth/login` (ví dụ 5 lần/phút/IP) — bắt buộc trước khi lên production để chống đoán mật khẩu. Gợi ý: middleware dùng Redis, hoặc làm ở API gateway/WAF.
4. Thêm endpoint `POST /auth/logout-all` gọi `_revoke_all` cho user hiện tại.

---

⬅️ Trước: [09 · Dependency Injection](09_DependencyInjection.md) · ➡️ Tiếp: [11 · Service Layer](11_ServiceLayer.md)
