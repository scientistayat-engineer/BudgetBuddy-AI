"""Backend tests. Run from the project root:  python -m pytest -q
No real network calls are made: Groq is replaced with httpx.MockTransport and
Firebase token verification is monkeypatched."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import main

BODY = {"messages": [{"role": "user", "content": "Where did I spend the most?"}]}


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    main._hits.clear()
    main._TRANSPORT = None
    yield
    main._TRANSPORT = None


@pytest.fixture
def client():
    return TestClient(main.app)


def mock_groq(status=200, content="Food was your top category.", seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": "x"})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    main._TRANSPORT = httpx.MockTransport(handler)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok" and data["ai_configured"] is True and data["auth_required"] is False


def test_ai_success_sends_key_and_model(client):
    seen = []
    mock_groq(seen=seen)
    r = client.post("/api/ai", json=BODY)
    assert r.status_code == 200 and r.json() == {"content": "Food was your top category."}
    sent = json.loads(seen[0].content)
    assert seen[0].headers["authorization"] == "Bearer gsk_test"
    assert sent["model"] == main.DEFAULT_MODEL and sent["temperature"] == 0.4
    assert "response_format" not in sent


def test_json_mode_sets_response_format(client):
    seen = []
    mock_groq(seen=seen, content='{"category":"Food"}')
    r = client.post("/api/ai", json={**BODY, "json_mode": True})
    assert r.status_code == 200
    sent = json.loads(seen[0].content)
    assert sent["response_format"] == {"type": "json_object"} and sent["temperature"] == 0


def test_missing_key_returns_503(client, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY")
    assert client.post("/api/ai", json=BODY).status_code == 503


@pytest.mark.parametrize(
    "upstream,expected", [(429, 429), (401, 502), (404, 502), (500, 502)]
)
def test_upstream_errors_are_mapped(client, upstream, expected):
    mock_groq(status=upstream)
    assert client.post("/api/ai", json=BODY).status_code == expected


@pytest.mark.parametrize(
    "bad",
    [
        {"messages": []},
        {"messages": [{"role": "hacker", "content": "hi"}]},
        {"messages": [{"role": "user", "content": ""}]},
        {"messages": [{"role": "user", "content": "x" * 12001}]},
        {"messages": [{"role": "user", "content": "hi"}] * 7},
    ],
)
def test_invalid_requests_rejected(client, bad):
    mock_groq()
    assert client.post("/api/ai", json=bad).status_code == 422


def test_auth_required_when_project_set(client, monkeypatch):
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-project")
    mock_groq()
    assert client.post("/api/ai", json=BODY).status_code == 401
    assert client.post("/api/ai", json=BODY, headers={"Authorization": "Basic abc"}).status_code == 401


def test_valid_and_invalid_tokens(client, monkeypatch):
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-project")
    mock_groq()

    def fake_verify(token, project_id):
        assert project_id == "demo-project"
        if token != "good-token":
            raise ValueError("bad token")
        return {"user_id": "uid-123"}

    monkeypatch.setattr(main, "verify_token", fake_verify)
    ok = client.post("/api/ai", json=BODY, headers={"Authorization": "Bearer good-token"})
    assert ok.status_code == 200
    bad = client.post("/api/ai", json=BODY, headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401


def test_rate_limit(client, monkeypatch):
    monkeypatch.setattr(main, "RATE_LIMIT", 3)
    mock_groq()
    codes = [client.post("/api/ai", json=BODY).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]


def test_cors_allows_configured_origin(client):
    r = client.options(
        "/api/ai",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
