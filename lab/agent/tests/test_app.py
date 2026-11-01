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
