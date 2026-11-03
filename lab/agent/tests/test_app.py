import subprocess
import sys
import textwrap

import pytest
import tidewatch_agent
import uvicorn
from fastapi.testclient import TestClient
from tidewatch_agent import app, hog_memory, main, memory_hog_enabled

FAKE_KEY = "fake-lab-key-0123456789abcdef"
client = TestClient(app)


@pytest.fixture
def no_server(monkeypatch):
    """main() would start uvicorn; record the call instead."""
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: calls.append((a, kw)))
    return calls


@pytest.fixture
def no_threads(monkeypatch):
    started = []

    class FakeThread:
        def __init__(self, *a, **kw):
            pass

        def start(self):
            started.append(True)

    monkeypatch.setattr(tidewatch_agent.threading, "Thread", FakeThread)
    return started


def test_health_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_chat_echoes_message():
    response = client.post("/chat", json={"message": "hello"})
    assert response.status_code == 200
    assert response.json() == {"reply": "echo: hello"}


def test_chat_rejects_missing_message():
    assert client.post("/chat", json={}).status_code == 422


def test_main_exits_when_api_key_missing(monkeypatch, capsys, no_server):
    monkeypatch.delenv("API_KEY", raising=False)
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 1
    assert "fatal: API_KEY is not set" in capsys.readouterr().err
    assert no_server == []


def test_main_exits_when_api_key_empty(monkeypatch, capsys, no_server):
    monkeypatch.setenv("API_KEY", "")
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 1
    assert "fatal: API_KEY is not set" in capsys.readouterr().err
    assert no_server == []


def test_main_never_prints_api_key(monkeypatch, capsys, no_server, no_threads):
    monkeypatch.setenv("API_KEY", FAKE_KEY)
    monkeypatch.delenv("MEMORY_HOG", raising=False)
    main()
    out = capsys.readouterr()
    assert len(no_server) == 1  # the server would have started
    assert FAKE_KEY not in out.out + out.err


def test_memory_hog_holds_requested_allocations():
    held: list[bytearray] = []
    hog_memory(held, step_mib=1, cap_mib=3, interval_s=0)
    assert len(held) == 3
    assert all(len(chunk) == 1024 * 1024 for chunk in held)


def test_memory_hog_stops_at_cap():
    held: list[bytearray] = []
    hog_memory(held, step_mib=1, cap_mib=2, interval_s=0)
    assert len(held) == 2


def test_memory_hog_off_unless_exactly_one(monkeypatch, no_server, no_threads):
    assert memory_hog_enabled({}) is False
    assert memory_hog_enabled({"MEMORY_HOG": "0"}) is False
    assert memory_hog_enabled({"MEMORY_HOG": "true"}) is False
    assert memory_hog_enabled({"MEMORY_HOG": "1"}) is True

    monkeypatch.setenv("API_KEY", FAKE_KEY)
    monkeypatch.delenv("MEMORY_HOG", raising=False)
    main()
    assert no_threads == []
    monkeypatch.setenv("MEMORY_HOG", "1")
    main()
    assert no_threads == [True]


def test_memory_hog_raises_resident_memory():
    # A fresh process, so the peak RSS (ru_maxrss) starts low and only this allocation counts.
    script = textwrap.dedent(
        """
        import resource, sys
        from tidewatch_agent import hog_memory
        unit = 1 if sys.platform == "darwin" else 1024  # bytes on macOS, KiB on Linux
        before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * unit
        held = []
        hog_memory(held, step_mib=10, cap_mib=50, interval_s=0)
        after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * unit
        print((after - before) // (1024 * 1024))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    grew_mib = int(result.stdout.strip())
    assert grew_mib >= 40, f"peak RSS grew only {grew_mib} MiB"


# --- upstream model calls --------------------------------------------------------------------

import io  # noqa: E402
import json as _json  # noqa: E402
import urllib.error  # noqa: E402

from tidewatch_agent import UpstreamError, call_model, upstream_check  # noqa: E402

URL = "http://model/v1/chat"


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener_returning(reply=None, error=None):
    seen = {}

    def opener(request, timeout):
        seen["request"], seen["timeout"] = request, timeout
        if error:
            raise error
        return _Response(_json.dumps({"reply": reply}).encode())

    return opener, seen


def http_error(code, reason, body=b"", headers=None):
    return urllib.error.HTTPError(URL, code, reason, headers or {}, io.BytesIO(body))


def test_chat_forwards_to_model_and_returns_reply(monkeypatch):
    opener, seen = opener_returning(reply="model: hi")
    monkeypatch.setenv("MODEL_URL", URL)
    monkeypatch.setenv("API_KEY", FAKE_KEY)
    monkeypatch.setattr(
        tidewatch_agent, "call_model", lambda message: call_model(message, opener=opener)
    )
    response = client.post("/chat", json={"message": "hi"})
    assert response.status_code == 200 and response.json() == {"reply": "model: hi"}
    assert seen["request"].full_url == URL
    assert _json.loads(seen["request"].data) == {"message": "hi"}
    assert seen["timeout"] == 5.0


def test_chat_returns_502_when_model_fails(monkeypatch):
    def failing(message):
        raise UpstreamError("401 Unauthorized (invalid API key)")

    monkeypatch.setattr(tidewatch_agent, "call_model", failing)
    response = client.post("/chat", json={"message": "hi"})
    assert response.status_code == 502
    assert response.json() == {
        "error": "upstream model call failed: 401 Unauthorized (invalid API key)"
    }


def test_upstream_check_logs_one_line_per_outcome(monkeypatch):
    monkeypatch.setenv("API_KEY", FAKE_KEY)
    cases = [
        (None, "INFO upstream model ok"),
        (
            http_error(401, "Unauthorized", b'{"error": "invalid API key"}'),
            "ERROR upstream model call failed: 401 Unauthorized (invalid API key)",
        ),
        (
            http_error(429, "Too Many Requests", headers={"Retry-After": "30"}),
            "ERROR upstream model call failed: 429 Too Many Requests (retry after 30s)",
        ),
        (TimeoutError("timed out"), "ERROR upstream model call failed: timed out after 5s"),
        (
            urllib.error.URLError(TimeoutError()),
            "ERROR upstream model call failed: timed out after 5s",
        ),
    ]
    for error, expected in cases:
        opener, _ = opener_returning(reply="model: ping", error=error)
        lines = []
        ok = upstream_check(lines.append, url=URL, timeout=5, opener=opener)
        assert lines == [expected]
        assert ok is (error is None)


def test_upstream_check_never_logs_the_api_key(monkeypatch):
    monkeypatch.setenv("API_KEY", FAKE_KEY)
    for error in (None, http_error(401, "Unauthorized", b'{"error": "invalid API key"}')):
        opener, seen = opener_returning(reply="model: ping", error=error)
        lines = []
        upstream_check(lines.append, url=URL, timeout=5, opener=opener)
        assert seen["request"].get_header("Authorization") == f"Bearer {FAKE_KEY}"
        assert all(FAKE_KEY not in line for line in lines)


def test_main_starts_upstream_check_only_with_model_url(monkeypatch, no_server, no_threads):
    monkeypatch.setenv("API_KEY", FAKE_KEY)
    monkeypatch.delenv("MEMORY_HOG", raising=False)
    monkeypatch.delenv("MODEL_URL", raising=False)
    main()
    assert no_threads == []
    monkeypatch.setenv("MODEL_URL", URL)
    main()
    assert no_threads == [True]
