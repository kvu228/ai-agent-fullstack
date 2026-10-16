# 08 · Nodes & `bind_tools`

## 🎯 Mục tiêu

- Hiểu **node** trong LangGraph: một callable `(state) -> partial update`.
- Hiểu vì sao `AgentNode` là **class** nhận model qua constructor (dependency injection).
- Hiểu `bind_tools` làm gì và `AIMessage.tool_calls` trông như thế nào.
- Hiểu `ToolNode` chạy tool và inject `ToolRuntime` ra sao.

## 📁 File liên quan

- `backend/app/agents/nodes.py` — `AgentNode`
- `backend/app/agents/graph.py` — nơi node được đăng ký
- `backend/tests/conftest.py` — `FakeToolCallingModel` inject vào node

---

## 1. Node là gì?

Node là **bất kỳ callable nào** nhận state và trả dict cập nhật: hàm thường, hàm async, object có
`__call__`, hoặc Runnable.

```python
async def my_node(state: AgentState) -> dict[str, Any]:
    return {"messages": [AIMessage("hello")]}
```

Graph của project có 2 node:

| Node | Code | Việc |
|---|---|---|
| `agent` | `AgentNode` (tự viết) | Hỏi LLM: trả lời luôn, hay gọi `search_documents`? |
| `tools` | `ToolNode` (prebuilt) | Chạy các tool mà LLM yêu cầu |

## 2. `AgentNode`

`backend/app/agents/nodes.py`:

```python
class AgentNode:
    """The "agent" node: ask the LLM what to do next (answer, or call tools).

    It is a class (not a plain function) so the model is *injected* in
    `__init__`, which keeps the node easy to test with a fake model.
    """

    def __init__(self, model: BaseChatModel, tools: Sequence[BaseTool]) -> None:
        # `bind_tools` sends the tools' JSON schemas to the LLM with every request.
        self._model = model.bind_tools(tools)

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        messages = [SystemMessage(SYSTEM_PROMPT), *state["messages"]]
        response = await self._model.ainvoke(messages)
        # Return only the *update*; the `add_messages` reducer appends it.
        return {"messages": [response]}
```

### Vì sao là class, không phải hàm + biến global?

Nhiều ví dụ trên mạng viết `model = init_chat_model(...)` ở cấp module. Hệ quả: import module là tạo
model thật (cần API key), test không thay được. Với class:

- `__init__` nhận **bất kỳ** `BaseChatModel` → production truyền Claude, test truyền fake.
- `bind_tools` chạy **một lần** khi dựng graph (trong lifespan), không lặp lại mỗi request.
- `__call__` là `async` → không chặn event loop của FastAPI.

Cùng tư tưởng với `Depends` của FastAPI và với tool factory ở bài [06](06_Tools.md).

## 3. `bind_tools`

```python
self._model = model.bind_tools(tools)
```

Trả về model "đã gắn" danh sách tool: mỗi lần gọi, tự đính kèm JSON schema của tool vào request
(Anthropic `tools=[...]`, OpenAI `tools=[{"type": "function", ...}]`). Model gốc không bị thay đổi.

Schema chỉ chứa `query` — tham số `runtime: ToolRuntime[AgentContext]` bị loại khỏi schema, nên LLM
không biết và không điền được `user_id`.

## 4. Output: `AIMessage` có thể chứa `tool_calls`

**a) Trả lời thẳng** (vd. "Chào bạn"):

```python
AIMessage(content="Xin chào! Bạn muốn tìm gì trong tài liệu?", tool_calls=[])
```

**b) Yêu cầu tìm kiếm:**

```python
>>> response.tool_calls
[{'name': 'search_documents', 'args': {'query': 'Atlas launch date'}, 'id': 'toolu_01', 'type': 'tool_call'}]
```

| Field | Ý nghĩa |
|---|---|
| `name` | Tên tool (khớp `tool.name`) |
| `args` | Tham số đã parse thành dict theo schema |
| `id` | `ToolMessage` trả lời phải mang đúng `tool_call_id` này |

`tool_calls` là field **chuẩn hoá** của LangChain — Anthropic (`tool_use` block) hay OpenAI (`function`)
đều đọc cùng cấu trúc. Một `AIMessage` có thể chứa **nhiều** tool call (vd. hai query khác nhau
cùng lúc), và có thể vừa có text ("Để tôi tìm trong tài liệu.") vừa có tool call.

Node `agent` không tự chạy tool — nó chỉ ghi `AIMessage` vào state. Rẽ nhánh sang `tools` là việc của
**edge** (bài [09](09_GraphAndReActLoop.md)).

## 5. `ToolNode` — node thực thi tool

```python
    builder.add_node(TOOLS_NODE, ToolNode(list(tools)))
```

`ToolNode` (từ `langgraph.prebuilt`) đọc `tool_calls` của `AIMessage` cuối, với mỗi call:

1. Tìm tool theo `name`.
2. Validate `args` theo schema.
3. **Inject** các tham số đặc biệt mà LLM không thấy: `ToolRuntime` (chứa `context` = `AgentContext`
   của lượt chạy hiện tại, `state`, `config`, `tool_call_id`…).
4. Chạy tool (các call song song với nhau), gói kết quả vào `ToolMessage`:

```python
ToolMessage(content="[atlas.pdf, p.2] (similarity 0.81)\n...", name="search_documents", tool_call_id="toolu_01")
```

Cùng một biến `tools` được truyền cho cả `AgentNode` (`bind_tools`) và `ToolNode` → schema LLM thấy
và tool thực thi không bao giờ lệch nhau.

## ⚠️ Lỗi thường gặp

- **Gọi model chưa bind** (`model.ainvoke` thay vì `self._model.ainvoke`) → LLM không biết có tool, không bao giờ search, trả lời "từ trí nhớ".
- **Danh sách tool ở `bind_tools` khác ở `ToolNode`** → LLM gọi tool mà `ToolNode` không có.
- **Tự tạo `ToolMessage` thiếu `tool_call_id`** → provider báo lỗi 400 ở lượt sau.
- **Gọi tool bằng tay ngoài graph** (`tool.ainvoke({"query": ...})`) → không có runtime được inject → lỗi. Muốn test tool, chạy qua graph hoặc `ToolNode` (bài [12](12_TestingAgents.md)).
- **Model không hỗ trợ tool calling** (một số model local nhỏ) → `bind_tools` raise `NotImplementedError`.

## 🧪 Thực hành

1. Trong REPL: dựng `vs` như bài 06, `m = get_chat_model(get_settings()).bind_tools(get_tools(vs))`, gọi `await m.ainvoke("Tài liệu nói gì về Atlas?")` và in `.tool_calls`.
2. Gọi `await m.ainvoke("Chào bạn")` — `tool_calls` có rỗng không? (Không có system prompt nên kết quả có thể khác khi chạy trong graph.)
3. Thử `bind_tools(tools, tool_choice="search_documents")` (ép luôn gọi tool), hỏi "chào bạn", quan sát. Đừng quên bỏ ra.

---

⬅️ Trước: [07 · State](07_State.md) · ➡️ Tiếp: [09 · Graph & ReAct loop](09_GraphAndReActLoop.md)
