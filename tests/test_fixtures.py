"""The lab fixtures (tests/fixtures/*.json, captured by lab/capture-fixtures.sh) are valid snapshots
and contain the evidence each scenario is supposed to produce."""

import re
from pathlib import Path

import pytest

from tidewatch.snapshot import SCHEMA_VERSION, Snapshot

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NAMES = [
    "healthy",
    "bad-image",
    "no-key",
    "memory",
    "llm-bad-key",
    "llm-rate-limit",
    "llm-timeout",
]
# The lab's fake API key is `openssl rand -hex 16`: 32 hex characters in a row.
KEY_LIKE = re.compile(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])")


def load(name: str) -> Snapshot:
    return Snapshot.model_validate_json((FIXTURES / f"{name}.json").read_text())


def pods_with(snap: Snapshot, app: str):
    """Pods by label, never by name (the tool must work for any app)."""
    return [p for p in snap.pods if p.labels.get("app") == app]


def agent_containers(snap: Snapshot):
    pods = pods_with(snap, "agent")
    assert len(pods) == 1, [p.name for p in snap.pods]
    return pods[0].containers


@pytest.mark.parametrize("name", NAMES)
def test_fixture_loads_as_a_snapshot_of_the_lab(name):
    snap = load(name)
    assert snap.schema_version == SCHEMA_VERSION
    assert (snap.context, snap.namespace) == ("kind-tidewatch-lab", "demo")
    assert [d.name for d in snap.deployments] == ["agent", "model"]
    assert (snap.log_lines, snap.log_since_seconds) == (50, 3600)


@pytest.mark.parametrize("name", NAMES)
def test_fixture_contains_no_key_like_values(name):
    assert KEY_LIKE.findall((FIXTURES / f"{name}.json").read_text()) == []


def test_healthy_fixture_is_ready_without_restarts_or_warnings():
    snap = load("healthy")
    (c,) = agent_containers(snap)
    assert c.ready and c.restart_count == 0 and c.state.state == "running"
    assert [e for e in snap.events if e.type == "Warning"] == []


def test_bad_image_fixture_captures_the_pull_failure():
    snap = load("bad-image")
    (c,) = agent_containers(snap)
    assert c.state.state == "waiting"
    assert c.state.reason in {"ErrImagePull", "ImagePullBackOff"}
    assert c.image.endswith(":0.0.0-missing")
    assert any(e.reason == "Failed" and "0.0.0-missing" in (e.message or "") for e in snap.events)


def test_no_key_fixture_captures_the_startup_exit_and_its_log_line():
    (c,) = agent_containers(load("no-key"))
    assert c.restart_count >= 1
    assert c.last_state and c.last_state.state == "terminated" and c.last_state.exit_code == 1
    assert "fatal: API_KEY is not set" in c.previous_logs


def test_memory_fixture_captures_the_oom_kill():
    snap = load("memory")
    (c,) = agent_containers(snap)
    assert c.restart_count >= 1
    assert c.last_state and c.last_state.reason == "OOMKilled"
    assert snap.deployments[0].containers[0].memory_limit == "64Mi"


def test_healthy_fixture_agent_reaches_the_model_and_model_has_no_access_log():
    snap = load("healthy")
    (agent,) = agent_containers(snap)
    upstream = [line for line in agent.recent_logs if " upstream model " in line]
    # The latest call succeeded. A one-off "connection failed" while the model is still starting
    # is a real startup race and allowed; auth, rate-limit and timeout failures are not.
    assert upstream and upstream[-1].endswith("INFO upstream model ok")
    for bad in ("401", "429", "timed out"):
        assert not any(bad in line for line in upstream), bad
    (model_pod,) = pods_with(snap, "model")
    assert not any("HTTP/1.1" in line for c in model_pod.containers for line in c.recent_logs)


@pytest.mark.parametrize(
    "name, expected",
    [
        ("llm-bad-key", "upstream model call failed: 401 Unauthorized (invalid API key)"),
        ("llm-rate-limit", "upstream model call failed: 429 Too Many Requests (retry after 30s)"),
        ("llm-timeout", "upstream model call failed: timed out after 5s"),
    ],
)
def test_llm_fixture_is_green_in_kubectl_but_logs_upstream_failures(name, expected):
    snap = load(name)
    (agent,) = agent_containers(snap)
    # The "all green" signal: running, ready, never restarted.
    assert agent.state.state == "running" and agent.ready and agent.restart_count == 0
    assert sum(expected in line for line in agent.recent_logs) >= 2
    (model_pod,) = pods_with(snap, "model")
    assert all(c.ready for c in model_pod.containers)
