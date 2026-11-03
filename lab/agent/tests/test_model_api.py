import threading
import time
import urllib.request

import pytest
import uvicorn
from fastapi.testclient import TestClient
from tidewatch_agent import model_api

client = TestClient(model_api.app)


def test_model_ok_replies(monkeypatch):
    monkeypatch.delenv("MODEL_MODE", raising=False)
    response = client.post("/v1/chat", json={"message": "hi"})
    assert response.status_code == 200 and response.json() == {"reply": "model: hi"}


def test_model_reject_key_returns_401(monkeypatch):
    monkeypatch.setenv("MODEL_MODE", "reject-key")
    response = client.post("/v1/chat", json={"message": "hi"})
    assert response.status_code == 401 and response.json() == {"error": "invalid API key"}


def test_model_rate_limit_returns_429_with_retry_after(monkeypatch):
    monkeypatch.setenv("MODEL_MODE", "rate-limit")
    response = client.post("/v1/chat", json={"message": "hi"})
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "30"


def test_model_slow_waits_before_replying(monkeypatch):
    waited = []

    async def fake_sleep(seconds):
        waited.append(seconds)

    monkeypatch.setenv("MODEL_MODE", "slow")
    monkeypatch.setenv("MODEL_SLOW_SECONDS", "30")
    monkeypatch.setattr(model_api.asyncio, "sleep", fake_sleep)
    response = client.post("/v1/chat", json={"message": "hi"})
    assert response.status_code == 200 and waited == [30.0]


def test_model_server_runs_without_access_log(monkeypatch):
    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: seen.update(kwargs))
    model_api.main()
    assert seen["access_log"] is False and seen["port"] == 8000


@pytest.fixture
def live_model(monkeypatch):
    monkeypatch.setenv("MODEL_MODE", "slow")
    monkeypatch.setenv("MODEL_SLOW_SECONDS", "3")
    server = uvicorn.Server(
        uvicorn.Config(model_api.app, host="127.0.0.1", port=0, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def test_model_health_answers_while_a_slow_chat_is_in_flight(live_model):
    def slow_call():
        request = urllib.request.Request(
            f"{live_model}/v1/chat",
            data=b'{"message": "hi"}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(request, timeout=10).read()

    slow = threading.Thread(target=slow_call)
    slow.start()
    time.sleep(0.3)  # the slow call is now waiting inside the server
    started = time.monotonic()
    with urllib.request.urlopen(f"{live_model}/health", timeout=2) as response:
        assert response.status == 200
    assert time.monotonic() - started < 1.0
    assert slow.is_alive()  # still waiting: /health didn't wait for it
    slow.join(timeout=10)
