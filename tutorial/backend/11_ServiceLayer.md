# 11 · Service Layer

## 🎯 Mục tiêu

- Hiểu vì sao logic nghiệp vụ nằm trong **service**, không nằm trong endpoint.
- Đọc `DocumentService`: validate file upload an toàn, đặt S3 key, **thứ tự** ghi/xoá giữa S3, Chroma và Postgres.
- Đọc `ChatService`: conversation trong Postgres + tin nhắn trong checkpointer LangGraph.
- Áp dụng nguyên tắc **phân quyền theo chủ sở hữu**: mọi truy vấn đều lọc `owner_id`, tài liệu của người khác trả **404**.

## 📁 File liên quan

- `backend/app/services/auth_service.py` (đã học ở bài 10)
- `backend/app/services/document_service.py`
- `backend/app/services/chat_service.py`
- `backend/app/ingestion/loaders.py` (`EXTENSION_TO_CONTENT_TYPE`)

---

## 1. Ba tầng

```
endpoints/   HTTP: parse input, status code, cookie       (bài 05)
services/    nghiệp vụ: quyền, validate, điều phối store   (bài này)
db / storage / vectorstore / agents   hạ tầng              (bài 07, 12, agent/*)
```

Service là class Python thuần: nhận `AsyncSession`, `S3Storage`… qua constructor, ném exception của domain (`NotFoundError`…), **không** import FastAPI `HTTPException`. Hệ quả:

- Test service không cần HTTP.
- `scripts/run_agent.py` hay một worker dùng lại được.
- Endpoint chỉ còn 2–3 dòng.

(`DocumentService.upload` nhận `UploadFile` của FastAPI — một ngoại lệ nhỏ, chấp nhận được vì nó chỉ là một file-like object.)

## 2. `DocumentService.upload` — không tin input

```python
    async def upload(self, owner: User, file: UploadFile) -> Document:
        # Strip any directory part ("../../etc/passwd" -> "passwd").
        filename = PurePath(file.filename or "untitled").name[:255]
        # Decide the type from the extension allow-list, never trust the client's header.
        content_type = EXTENSION_TO_CONTENT_TYPE.get(PurePath(filename).suffix.lower())
        if content_type is None:
            allowed = ", ".join(EXTENSION_TO_CONTENT_TYPE)
            raise ValidationFailedError(f"Unsupported file type. Allowed: {allowed}")
```

- **Path traversal**: tên file do client gửi; `PurePath(...).name` bỏ mọi phần thư mục. Test `test_filename_path_traversal_is_stripped`: `"../../etc/notes.txt"` → `"notes.txt"`.
- `[:255]`: khớp `String(255)` của cột `filename`.
- **Allow-list** theo đuôi file (`.pdf`, `.txt`, `.md`), không dùng header `Content-Type` client gửi — header đó ai cũng giả được. Loader được chọn theo `content_type` này ([agent/03](../agent/03_DocumentLoadersAndChunking.md)).

```python
        # Read at most max+1 bytes so a huge upload can't exhaust memory.
        data = await file.read(self._max_bytes + 1)
        if len(data) > self._max_bytes:
            raise PayloadTooLargeError(f"File exceeds {self._max_bytes // (1024 * 1024)} MB")
        if not data:
            raise ValidationFailedError("File is empty")
```

Mẹo **đọc max + 1 byte**: đọc được nhiều hơn giới hạn = file quá lớn, mà không phải nạp cả file 2 GB vào RAM. → 413 (`MAX_UPLOAD_MB`, bài 02). Ở production nên đặt thêm giới hạn body ở reverse proxy/ALB.

```python
        document_id = uuid.uuid4()
        document = Document(
            id=document_id,
            owner_id=owner.id,
            ...
            # Random id in the key: no collisions, user-supplied names can't clash.
            s3_key=f"users/{owner.id}/documents/{document_id}/{filename}",
            status=DocumentStatus.PROCESSING,
        )
        # Upload first: if S3 fails we have no orphan DB row pointing to nothing.
        await self._storage.upload(document.s3_key, data, content_type)
        self._session.add(document)
        await self._session.commit()
        return document
```

- S3 key có prefix `users/{owner_id}/` → dễ áp IAM policy, lifecycle rule hay xoá toàn bộ dữ liệu một user.
- **Thứ tự** khi có nhiều store mà không có transaction chung: ghi S3 trước, DB sau. S3 lỗi → không có dòng DB "ma" trỏ tới file không tồn tại. DB lỗi → còn một object S3 mồ côi — vô hại với người dùng, dọn được bằng job định kỳ.
- Trả về document `processing`; endpoint gắn `ingestion.run` vào `BackgroundTasks` (bài 05, [agent/05](../agent/05_IngestionPipeline.md)).

## 3. Phân quyền theo chủ sở hữu

```python
    async def get(self, owner: User, document_id: uuid.UUID) -> Document:
        document = await self._session.scalar(
            select(Document).where(Document.id == document_id, Document.owner_id == owner.id)
        )
        if document is None:
            raise NotFoundError("Document not found")
        return document
```

- Điều kiện `owner_id` nằm **trong câu truy vấn**, không phải "tải về rồi so sánh" → không thể quên.
- Tài liệu của người khác → **404**, không phải 403. 403 nghĩa là "có tồn tại nhưng không phải của bạn" — để lộ thông tin. Test `test_users_cannot_see_each_other_documents`.
- `download_url` và `delete` đều đi qua `get()` → kế thừa kiểm tra quyền.
- Cùng nguyên tắc ở Chroma: tool tìm kiếm luôn lọc `user_id` ([agent/06](../agent/06_Tools.md)).

## 4. Xoá: thứ tự ngược lại

```python
    async def delete(self, owner: User, document_id: uuid.UUID) -> None:
        document = await self.get(owner, document_id)
        # Remove derived data first; the DB row goes last so a failure can be retried.
        await self._vector_store.delete_document(document.id, document.chunk_count)
        await self._storage.delete(document.s3_key)
        await self._session.delete(document)
        await self._session.commit()
```

Chroma → S3 → Postgres. Nếu xoá S3 lỗi, dòng DB vẫn còn → người dùng thấy tài liệu và bấm xoá lại được (các bước trước idempotent). Nếu xoá DB trước, các chunk còn lại trong Chroma sẽ **vẫn được agent tìm thấy** mà không ai xoá được nữa.

## 5. `ChatService` — hai nơi lưu trữ

```python
def _config(conversation_id: uuid.UUID) -> RunnableConfig:
    # `thread_id` tells the checkpointer which conversation to load/save.
    return {"configurable": {"thread_id": str(conversation_id)}}
```

Bảng `conversations` giữ **chủ sở hữu + tiêu đề**; checkpointer giữ **tin nhắn**, khoá bằng `thread_id == str(conversation.id)` ([agent/10](../agent/10_MemoryPostgresCheckpointer.md)). Mọi truy cập checkpointer đều đi qua `get_conversation()` trước để kiểm tra quyền.

```python
    async def get_or_create_conversation(
        self, owner: User, conversation_id: uuid.UUID | None, first_message: str
    ) -> Conversation:
        if conversation_id is not None:
            conversation = await self.get_conversation(owner, conversation_id)
            conversation.updated_at = utcnow()  # most recently used first in the sidebar
        else:
            conversation = Conversation(owner_id=owner.id, title=first_message.strip()[:80])
            self._session.add(conversation)
        await self._session.commit()
        return conversation
```

- Không có `conversation_id` → tạo mới, tiêu đề là 80 ký tự đầu của tin nhắn.
- Gán `updated_at` thủ công: `onupdate` chỉ chạy khi có cột khác thay đổi; ở đây không đổi gì khác.
- Được gọi từ dependency `get_chat_conversation`, **trước** khi stream (bài 09).

```python
    async def messages(self, owner: User, conversation_id: uuid.UUID) -> list[ChatMessage]:
        conversation = await self.get_conversation(owner, conversation_id)  # ownership check
        snapshot = await self._graph.aget_state(_config(conversation.id))
        for msg in snapshot.values.get("messages", []):
            if isinstance(msg, HumanMessage):
                result.append(ChatMessage(role="user", content=msg.text))
            elif isinstance(msg, AIMessage) and msg.text:
                result.append(ChatMessage(role="assistant", content=msg.text))
```

Lịch sử đọc thẳng từ state của graph; bỏ `ToolMessage` và `AIMessage` chỉ chứa tool call (không có text) — UI chỉ cần hội thoại người–máy.

```python
        # Delete the stored messages too, not just our row (privacy: "delete" means delete).
        if isinstance(self._graph.checkpointer, BaseCheckpointSaver):
            await self._graph.checkpointer.adelete_thread(str(conversation.id))
```

Xoá conversation mà để lại checkpoint = dữ liệu cá nhân vẫn nằm trong DB. `adelete_thread` xoá mọi checkpoint của thread.

`stream()` chạy graph với `context=AgentContext(user_id=owner.id)` (tool biết đang tìm cho ai) và chuyển chunk/update của LangGraph thành `StreamEvent` — chi tiết ở [agent/11](../agent/11_Streaming.md), phần HTTP ở bài 13.

## ⚠️ Lỗi thường gặp

- **`select(Document).where(Document.id == id)` quên `owner_id`** → lỗ hổng IDOR kinh điển: ai biết id là đọc được.
- **Tin `file.content_type`** → upload file `.exe` với header `text/plain`.
- **`await file.read()` không giới hạn** → một request làm hết RAM.
- **Ném `HTTPException` trong service** → service bị trói vào FastAPI; dùng exception domain (bài 14).
- **Xoá conversation chỉ trong bảng của mình** → tin nhắn vẫn còn trong `checkpoints`.

## 🧪 Thực hành

1. Upload một file `.txt` rỗng và một file lớn hơn `MAX_UPLOAD_MB`; đối chiếu status code với code ở mục 2.
2. Thêm method `rename_conversation(owner, conversation_id, title)` dùng `get_conversation()` để kiểm quyền; viết test cho trường hợp người khác gọi (phải 404).
3. Viết script dọn object S3 mồ côi: liệt kê key dưới `users/`, so với cột `documents.s3_key`.

---

⬅️ Trước: [10 · Authentication](10_Authentication.md) · ➡️ Tiếp: [12 · Blob Storage với S3](12_BlobStorageS3.md)
