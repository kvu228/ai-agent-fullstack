import json
from typing import Any

from httpx import AsyncClient
from langchain_core.messages import AIMessage

from tests.conftest import FakeToolCallingModel, register_and_login
from tests.test_documents import upload


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    """Turn a raw `text/event-stream` body into [(event, data), ...]."""
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in fields:  # skip comment-only blocks such as ": ping" keep-alives
            events.append((fields["event"], json.loads(fields["data"])))
    return events


def search_then_answer(answer: str) -> list[AIMessage]:
    return [
        AIMessage(
            "Let me search.",
            tool_calls=[{"id": "c1", "name": "search_documents", "args": {"query": "Atlas"}}],
        ),
        AIMessage(answer),
    ]


async def chat(client: AsyncClient, message: str, conversation_id: str | None = None) -> Any:
    body = {"message": message, "conversation_id": conversation_id}
    async with client.stream("POST", "/api/v1/chat/stream", json=body) as response:
        assert response.status_code == 200, await response.aread()
        # Proxies must not gzip (= buffer) the stream.
        assert "no-transform" in response.headers.get_list("cache-control")
        return parse_sse((await response.aread()).decode())


async def test_chat_searches_own_documents(
    client: AsyncClient, fake_llm: FakeToolCallingModel
) -> None:
    await register_and_login(client)
    await upload(client)
    fake_llm.messages = iter(search_then_answer("Atlas launches in March [atlas.txt]."))

    events = await chat(client, "When does Atlas launch?")

    types = [e for e, _ in events]
    assert types[0] == "metadata" and types[-1] == "done"
    assert ("tool_start", {"tool_name": "search_documents", "tool_input": {"query": "Atlas"}}) in (
        events
    )
    tool_output = next(d["content"] for e, d in events if e == "tool_end")
    assert "[atlas.txt]" in tool_output and "billing platform" in tool_output
    answer = "".join(d["content"] for e, d in events if e == "token")
    assert answer.endswith("Atlas launches in March [atlas.txt].")


async def test_search_never_returns_other_users_documents(
    client: AsyncClient, make_client: Any, fake_llm: FakeToolCallingModel
) -> None:
    await register_and_login(client, "alice@example.com")
    await upload(client)  # Alice's document

    async with make_client() as bob:
        await register_and_login(bob, "bob@example.com")
        fake_llm.messages = iter(search_then_answer("I found nothing."))
        events = await chat(bob, "When does Atlas launch?")

    tool_output = next(d["content"] for e, d in events if e == "tool_end")
    assert "billing platform" not in tool_output
    assert "No relevant passages" in tool_output


async def test_conversation_is_persisted_and_listed(
    client: AsyncClient, fake_llm: FakeToolCallingModel
) -> None:
    await register_and_login(client)
    fake_llm.messages = iter([AIMessage("First answer"), AIMessage("Second answer")])

    events = await chat(client, "Hello there")
    conversation_id = events[0][1]["conversation_id"]
    await chat(client, "And again", conversation_id)

    conversations = (await client.get("/api/v1/conversations")).json()
    assert [(c["id"], c["title"]) for c in conversations] == [(conversation_id, "Hello there")]

    history = (await client.get(f"/api/v1/conversations/{conversation_id}/messages")).json()
    assert [m["content"] for m in history["messages"]] == [
        "Hello there",
        "First answer",
        "And again",
        "Second answer",
    ]


async def test_cannot_use_someone_elses_conversation(
    client: AsyncClient, make_client: Any, fake_llm: FakeToolCallingModel
) -> None:
    await register_and_login(client, "alice@example.com")
    fake_llm.messages = iter([AIMessage("Hi Alice")])
    conversation_id = (await chat(client, "Hi"))[0][1]["conversation_id"]

    async with make_client() as bob:
        await register_and_login(bob, "bob@example.com")
        response = await bob.post(
            "/api/v1/chat/stream", json={"message": "hi", "conversation_id": conversation_id}
        )
        assert response.status_code == 404  # checked before the stream starts
        messages_url = f"/api/v1/conversations/{conversation_id}/messages"
        assert (await bob.get(messages_url)).status_code == 404


async def test_delete_conversation(client: AsyncClient, fake_llm: FakeToolCallingModel) -> None:
    await register_and_login(client)
    fake_llm.messages = iter([AIMessage("Hi")])
    conversation_id = (await chat(client, "Hi"))[0][1]["conversation_id"]

    response = await client.delete(f"/api/v1/conversations/{conversation_id}")
    assert response.status_code == 204
    assert (await client.get("/api/v1/conversations")).json() == []
