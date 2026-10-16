# 13 · Streaming câu trả lời bằng Server-Sent Events

## 🎯 Mục tiêu

- Hiểu **SSE** (Server-Sent Events) và vì sao chọn nó thay vì WebSocket cho chat.
- Dùng `EventSourceResponse` + `ServerSentEvent` có sẵn trong FastAPI.
- Thiết kế giao thức event: `metadata → (token | tool_start | tool_end)* → done | error`.
- Đặt header đúng **trước** khi stream bắt đầu, và hiểu vì sao **gzip giết streaming**.
- Đo xem token có thực sự đến từng cái một hay không.

## 📁 File liên quan

- `backend/app/api/v1/endpoints/chat.py`
- `backend/app/services/chat_service.py` (`stream`)
- `backend/app/schemas/chat.py` (`StreamEvent`)
- `backend/tests/test_chat.py` (`parse_sse`)

---

## 1. SSE là gì?

Một response HTTP bình thường, `Content-Type: text/event-stream`, không bao giờ "xong" cho tới khi server đóng. Server ghi từng khối văn bản:

```
event: metadata
data: {"conversation_id":"6f1c..."}

event: token
data: {"content":"Xin"}

event: token
data: {"content":" chào"}

event: done
data: {"conversation_id":"6f1c..."}

```

Mỗi event kết thúc bằng **một dòng trống**. So với WebSocket: SSE là HTTP thường (đi qua proxy, cookie, auth như mọi request), một chiều server → client — đúng nhu cầu "gửi câu hỏi, nhận câu trả lời dần dần". Frontend gửi bằng `fetch` POST và tự parse stream ([frontend/08](../frontend/08_ParsingSSE.md)), vì `EventSource` của trình duyệt chỉ hỗ trợ GET.

## 2. Endpoint

```python
@router.post(
    "/chat/stream",
    response_class=EventSourceResponse,
    dependencies=[Depends(_no_transform)],
)
async def chat_stream(
    body: ChatRequest,
    user: CurrentUser,
    conversation: ChatConversation,
    service: ChatServiceDep,
) -> AsyncIterator[ServerSentEvent]:
    """Send a message and receive the answer token-by-token as Server-Sent Events."""
    async for event in service.stream(user, conversation, body.message):
        yield ServerSentEvent(
            event=event.type,
            data=event.model_dump(mode="json", exclude_none=True, exclude={"type"}),
        )
```

- `response_class=EventSourceResponse` + endpoint là **async generator** → FastAPI (`fastapi.sse`) biến mỗi `yield` thành một event SSE và flush ngay.
- `event=event.type` → dòng `event:`; phần còn lại thành JSON ở `data:`. `exclude_none=True`: event `token` chỉ có `content`, không kèm một loạt `null`.
- `mode="json"`: UUID → chuỗi.
- FastAPI tự gửi comment `: ping` mỗi 15 giây khi generator im lặng (ví dụ lúc tool đang chạy lâu) để proxy không cắt kết nối vì idle. `parse_sse` trong test bỏ qua các block chỉ có comment.

Mọi kiểm tra có thể thất bại — đăng nhập, quyền sở hữu conversation, validate body — đều nằm ở **dependency** (bài 09), chạy xong trước khi byte đầu tiên được gửi.

## 3. Giao thức event

| `event` | `data` | Khi nào |
| --- | --- | --- |
| `metadata` | `conversation_id` | Ngay đầu: frontend biết id của conversation mới tạo |
| `token` | `content` | Mỗi mẩu text LLM sinh ra |
| `tool_start` | `tool_name`, `tool_input` | Agent quyết định gọi tool (ví dụ `search_documents`) |
| `tool_end` | `tool_name`, `content` | Tool trả kết quả |
| `done` | `conversation_id` | Kết thúc bình thường |
| `error` | `content` | Có lỗi giữa chừng |

Cách `ChatService.stream` tạo các event này từ `stream_mode=["messages", "updates"]` của LangGraph: [agent/11](../agent/11_Streaming.md).

### Lỗi "in-band"

```python
        except Exception:
            # Headers (200 OK) are already sent, so we report errors in-band.
            logger.exception("Agent stream failed")
            yield StreamEvent(type="error", content="The assistant failed to answer.")
            return
```

Khi đã stream, status `200` không rút lại được. Lỗi (OpenAI timeout, Chroma sập…) được báo bằng một **event** `error`. Thông báo chung chung cho client, chi tiết vào log — không lộ stack trace.

## 4. Header: `no-cache`, `X-Accel-Buffering`, và `no-transform`

FastAPI tự đặt cho mọi SSE response (`fastapi/routing.py`):

```python
                response.headers["Cache-Control"] = "no-cache"
                # For Nginx proxies to not buffer server sent events
                response.headers["X-Accel-Buffering"] = "no"
                response.headers.raw.extend(solved_result.response.headers.raw)
```

Dòng cuối: header mà **dependency** đặt lên tham số `response: Response` được *nối thêm* vào. Project thêm `no-transform`:

```python
def _no_transform(response: Response) -> None:
    """FastAPI already sends `Cache-Control: no-cache` for SSE. `no-transform` also tells
    proxies not to gzip the stream: compression (Next.js `next start`, CDNs) buffers the
    events, and the user would get the whole answer at once instead of token by token.

    Must be a dependency: code inside the generator body runs after headers are sent.
    """
    response.headers["Cache-Control"] = "no-transform"
```

Response cuối cùng có hai dòng `cache-control` (`no-cache` và `no-transform`); qua proxy Next.js nó thành `cache-control: no-cache, no-transform`.

### Câu chuyện phía sau

Khi kiểm thử end-to-end qua proxy của Next.js ([frontend/05](../frontend/05_BackendProxyRewrites.md)):

- `next dev`: token đến từng cái — tốt.
- `next start` (production): toàn bộ câu trả lời đến **một lúc** ở cuối. Response có `Content-Encoding: gzip` — trình duyệt luôn gửi `Accept-Encoding: gzip`, và Next.js nén response; bộ nén gom dữ liệu thành khối lớn trước khi gửi.

Với một model giả trả token cách nhau 0,4 s, các dòng `token` đến cách nhau khoảng **0,03 s** (tức là dồn cục) thay vì 0,4 s. `Cache-Control: no-transform` báo cho proxy/CDN "không được biến đổi body", và Next.js bỏ qua nén → token lại cách nhau 0,4 s, không còn `Content-Encoding: gzip`.

Lần thử đầu tiên đặt header **bên trong** thân generator — không có tác dụng: thân generator chỉ chạy khi response đã bắt đầu, header đã gửi đi rồi. Vì thế phải là một dependency.

## 5. Đo streaming

```bash
TOKEN=...   # access_token từ /auth/login
curl -sN -X POST http://localhost:3000/api/v1/chat/stream \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -H "Accept-Encoding: gzip" \
  -d '{"message":"Tóm tắt tài liệu của tôi"}' \
  | while IFS= read -r line; do echo "$(python3 -c 'import time;print(f"{time.time():.2f}")') $line"; done
```

- `-N`: tắt buffer của curl. `Accept-Encoding: gzip` để mô phỏng trình duyệt.
- Mỗi dòng có timestamp; nếu mọi `event: token` cùng một giây → có ai đó đang buffer.
- Gọi qua `:3000` (proxy) **và** `:8000` (trực tiếp) để khoanh vùng thủ phạm. Thêm `-D -` để xem header.

## 6. Test

```python
    async with client.stream("POST", "/api/v1/chat/stream", json=body) as response:
        assert response.status_code == 200, await response.aread()
        # Proxies must not gzip (= buffer) the stream.
        assert "no-transform" in response.headers.get_list("cache-control")
        return parse_sse((await response.aread()).decode())
```

`parse_sse` tách body theo dòng trống thành `[(event, data), ...]` để assert thứ tự event (`metadata` đầu, `done` cuối, có `tool_start` cho `search_documents`). `get_list` vì có **hai** header `cache-control`.

## ⚠️ Lỗi thường gặp

- **Kiểm tra quyền bên trong generator** → không trả được 404/403, client nhận 200 rồi một event lỗi.
- **Nginx/ALB/CDN buffer**: cần `X-Accel-Buffering: no` (Nginx), tắt nén cho `text/event-stream`, timeout idle > 15 s.
- **Gọi code đồng bộ chậm trong generator** → chặn event loop, mọi stream khác cũng đứng.
- **Không xử lý client ngắt kết nối**: khi người dùng bấm Stop, request bị huỷ và generator nhận `CancelledError`; đừng nuốt exception đó (`except Exception` không bắt `CancelledError` — đúng ý).

## 🧪 Thực hành

1. Chạy lệnh ở mục 5 qua `:8000` rồi qua `:3000`; so sánh header `content-encoding` và khoảng cách thời gian.
2. Tạm xoá `dependencies=[Depends(_no_transform)]`, build frontend (`npm run build && npm start`), chạy lại lệnh qua `:3000`; quan sát token dồn cục. Đặt lại.
3. Thêm event `usage` (số token input/output) gửi ngay trước `done`: sửa `StreamEventType`, `ChatService.stream`, và type bên frontend.

---

⬅️ Trước: [Agent 13 · Next Steps](../agent/13_NextSteps.md) · ➡️ Tiếp: [14 · Error Handling & Health Checks](14_ErrorHandlingAndHealthChecks.md)
