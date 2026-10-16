from typing import Any

from httpx import AsyncClient

from tests.conftest import register_and_login

TEXT = (
    "Project Atlas is the internal codename for the new billing platform. "
    "It launches in March. The project lead is Minh Tran. " * 5
).encode()


async def upload(client: AsyncClient, name: str = "atlas.txt", data: bytes = TEXT) -> Any:
    return await client.post("/api/v1/documents", files={"file": (name, data, "text/plain")})


async def test_upload_is_ingested(client: AsyncClient) -> None:
    await register_and_login(client)
    response = await upload(client)
    assert response.status_code == 202
    document_id = response.json()["id"]

    # Background ingestion has finished once the ASGI call returns.
    document = (await client.get(f"/api/v1/documents/{document_id}")).json()
    assert document["status"] == "ready", document
    assert document["chunk_count"] > 1
    assert "s3_key" not in document

    listing = (await client.get("/api/v1/documents")).json()
    assert [d["id"] for d in listing] == [document_id]


async def test_rejects_unsupported_type(client: AsyncClient) -> None:
    await register_and_login(client)
    response = await upload(client, name="virus.exe")
    assert response.status_code == 422


async def test_rejects_too_large_file(client: AsyncClient) -> None:
    await register_and_login(client)
    response = await upload(client, data=b"x" * (1024 * 1024 + 1))  # limit is 1 MB in tests
    assert response.status_code == 413


async def test_filename_path_traversal_is_stripped(client: AsyncClient) -> None:
    await register_and_login(client)
    response = await upload(client, name="../../etc/notes.txt")
    assert response.json()["filename"] == "notes.txt"


async def test_download_url(client: AsyncClient) -> None:
    await register_and_login(client)
    document_id = (await upload(client)).json()["id"]
    response = await client.get(f"/api/v1/documents/{document_id}/download")
    assert response.status_code == 200
    assert "test-documents" in response.json()["url"]


async def test_users_cannot_see_each_other_documents(client: AsyncClient, make_client: Any) -> None:
    await register_and_login(client, "alice@example.com")
    document_id = (await upload(client)).json()["id"]

    async with make_client() as bob:
        await register_and_login(bob, "bob@example.com")
        assert (await bob.get("/api/v1/documents")).json() == []
        # 404, not 403: don't even confirm the document exists.
        assert (await bob.get(f"/api/v1/documents/{document_id}")).status_code == 404
        assert (await bob.delete(f"/api/v1/documents/{document_id}")).status_code == 404


async def test_delete_document(client: AsyncClient) -> None:
    await register_and_login(client)
    document_id = (await upload(client)).json()["id"]

    assert (await client.delete(f"/api/v1/documents/{document_id}")).status_code == 204
    assert (await client.get(f"/api/v1/documents/{document_id}")).status_code == 404
