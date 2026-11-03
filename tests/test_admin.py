import httpx
import respx
from fastapi.testclient import TestClient

from mi_health.api.admin import LOGIN_PREFIX, LOGIN_URL_ENDPOINT
from mi_health.api.app import create_app


def _login(client: TestClient) -> str:
    response = client.post("/api/auth/login", json={"password": "secret"})
    assert response.status_code == 200
    return response.json()["data"]["csrf_token"]


def test_admin_login_and_key_management(ctx):
    settings, db, store = ctx
    app = create_app(settings, db, store, mcp=None)
    client = TestClient(app)

    assert client.get("/api/keys").status_code == 401
    assert client.post("/api/auth/login", json={"password": "bad"}).status_code == 401
    csrf = _login(client)
    assert client.get("/api/auth/me").json()["data"]["authenticated"] is True
    assert client.get("/api/status").status_code == 200
    assert client.get("/api/summary?start_date=2026-11-03&end_date=2026-11-03").status_code == 200
    assert client.post("/api/sync", json={}).status_code == 403
    assert client.post("/api/sync", json={}, headers={"X-CSRF-Token": csrf}).status_code == 202
    assert client.post("/api/keys", json={"scopes": "read"}).status_code == 403

    response = client.post(
        "/api/keys", json={"label": "agent", "scopes": ["read", "sync"]}, headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["token"].startswith("mhk_")
    assert data["scopes"] == ["read", "sync"]
    assert len(client.get("/api/keys").json()["data"]) == 1

    response = client.delete(f"/api/keys/{data['prefix']}", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert client.get("/api/keys").json()["data"][0]["revoked"] == 1


@respx.mock
def test_qr_login_stores_encrypted_credentials(ctx):
    settings, db, store = ctx
    app = create_app(settings, db, store, mcp=None)
    client = TestClient(app)
    csrf = _login(client)

    respx.get(LOGIN_URL_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            text=LOGIN_PREFIX
            + '{"lp":"https://lp.example/poll","loginUrl":"https://login.example/qr","timeout":300}',
        )
    )
    start = client.post("/api/auth/qr/start", headers={"X-CSRF-Token": csrf})
    assert start.status_code == 200
    token = start.json()["data"]["qr_token"]
    assert start.json()["data"]["login_url"] == "https://login.example/qr"

    respx.get("https://lp.example/poll").mock(
        return_value=httpx.Response(
            200,
            text=LOGIN_PREFIX + '{"code":0,"userId":"u1","passToken":"pt"}',
        )
    )
    poll = client.get(f"/api/auth/qr/poll?token={token}")
    assert poll.status_code == 200
    assert poll.json()["data"]["status"] == "confirmed"
    assert store.get().pass_token == "pt"
