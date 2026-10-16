from httpx import AsyncClient

from tests.conftest import register_and_login

CREDS = {"email": "Alice@Example.com", "password": "correct-horse-battery"}


async def test_register_login_and_me(client: AsyncClient) -> None:
    response = await client.post("/api/v1/auth/register", json=CREDS)
    assert response.status_code == 201
    assert response.json()["email"] == "alice@example.com"  # normalised
    assert "hashed_password" not in response.json()

    response = await client.post("/api/v1/auth/login", json=CREDS)
    assert response.status_code == 200
    set_cookie = response.headers.get_list("set-cookie")
    assert any(c.startswith("access_token=") and "HttpOnly" in c for c in set_cookie)
    assert any(c.startswith("refresh_token=") and "HttpOnly" in c for c in set_cookie)

    me = await client.get("/api/v1/auth/me")  # authenticated by the cookie
    assert me.status_code == 200
    assert me.json()["email"] == "alice@example.com"


async def test_register_duplicate_email(client: AsyncClient) -> None:
    await client.post("/api/v1/auth/register", json=CREDS)
    response = await client.post("/api/v1/auth/register", json=CREDS)
    assert response.status_code == 409


async def test_register_rejects_short_password(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register", json={"email": "a@example.com", "password": "short"}
    )
    assert response.status_code == 422


async def test_login_wrong_password_and_unknown_email_look_the_same(client: AsyncClient) -> None:
    await client.post("/api/v1/auth/register", json=CREDS)
    wrong = await client.post("/api/v1/auth/login", json={**CREDS, "password": "nope-nope-nope"})
    unknown = await client.post(
        "/api/v1/auth/login", json={"email": "bob@example.com", "password": "whatever-123"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


async def test_protected_route_requires_auth(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (await client.get("/api/v1/documents")).status_code == 401


async def test_bearer_token_works_without_cookies(client: AsyncClient) -> None:
    await client.post("/api/v1/auth/register", json=CREDS)
    token = (await client.post("/api/v1/auth/login", json=CREDS)).json()["access_token"]
    client.cookies.clear()

    response = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200


async def test_tampered_token_is_rejected(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not.a.valid-token"}
    )
    assert response.status_code == 401


async def test_refresh_rotates_and_detects_reuse(client: AsyncClient) -> None:
    await register_and_login(client)
    old_refresh = client.cookies["refresh_token"]

    response = await client.post("/api/v1/auth/refresh")
    assert response.status_code == 200
    new_refresh = client.cookies["refresh_token"]
    assert new_refresh != old_refresh

    # Replaying the old (rotated) token = theft signal -> every session is revoked.
    client.cookies.clear()
    replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert replay.status_code == 401
    after = await client.post("/api/v1/auth/refresh", json={"refresh_token": new_refresh})
    assert after.status_code == 401


async def test_logout_revokes_refresh_token(client: AsyncClient) -> None:
    await register_and_login(client)
    refresh_token = client.cookies["refresh_token"]

    response = await client.post("/api/v1/auth/logout")
    assert response.status_code == 204
    reuse = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert reuse.status_code == 401


async def test_cross_site_cookie_request_is_blocked(client: AsyncClient) -> None:
    await register_and_login(client)
    response = await client.post(
        "/api/v1/chat/stream",
        json={"message": "hi"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
