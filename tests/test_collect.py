import re
from datetime import timedelta

import pytest
from fakes import (
    ALLOWED,
    ALLOWED_APPS,
    T0,
    FakeApps,
    FakeCore,
    container_status,
    deployment,
    event,
    pod,
    state,
)
from kubernetes.client.exceptions import ApiException

from tidewatch.collect import LOG_LINES, LOG_SINCE_SECONDS, NamespaceNotFound, collect
from tidewatch.snapshot import SCHEMA_VERSION


def test_collect_builds_snapshot_from_api_objects():
    core = FakeCore(pods=[pod()], events=[event()])
    apps = FakeApps(deployments=[deployment()])
    snap = collect(core, apps, "demo", context="kind-tidewatch-lab", now=T0)

    assert snap.schema_version == SCHEMA_VERSION
    assert (snap.namespace, snap.context, snap.collected_at) == ("demo", "kind-tidewatch-lab", T0)
    (p,) = snap.pods
    assert p.name == "agent-abc" and p.phase == "Running"
    assert p.owner == "ReplicaSet/agent-rs"
    assert p.conditions == {"Ready": "True", "PodScheduled": "True"}
    (c,) = p.containers
    assert (c.name, c.image, c.ready, c.restart_count) == (
        "agent",
        "tidewatch-agent:0.1.0",
        True,
        0,
    )
    assert c.state.state == "running"
    (d,) = snap.deployments
    assert (d.name, d.replicas, d.ready_replicas, d.available_replicas) == ("agent", 1, 1, 1)
    assert d.containers[0].memory_limit == "128Mi" and d.containers[0].memory_request == "64Mi"
    assert d.containers[0].cpu_limit == "250m" and d.containers[0].cpu_request == "50m"
    (e,) = snap.events
    assert (e.type, e.reason, e.object, e.count) == ("Normal", "Scheduled", "Pod/agent-abc", 1)
    assert e.first_seen == T0 and e.last_seen == T0


def test_collect_maps_waiting_and_terminated_states():
    pulling = container_status(ready=False, st=state(waiting=("ImagePullBackOff", "Back-off")))
    oom = container_status(
        name="hog",
        ready=False,
        restarts=3,
        st=state(waiting=("CrashLoopBackOff", None)),
        last=state(terminated=("OOMKilled", 137)),
    )
    core = FakeCore(pods=[pod(statuses=[pulling, oom], ready="False")], logs="")
    snap = collect(core, FakeApps(), "demo", now=T0)
    first, second = snap.pods[0].containers
    assert first.state.state == "waiting" and first.state.reason == "ImagePullBackOff"
    assert first.state.message == "Back-off" and first.last_state is None
    assert second.state.reason == "CrashLoopBackOff"
    assert second.last_state.state == "terminated"
    assert (second.last_state.reason, second.last_state.exit_code) == ("OOMKilled", 137)


def test_collect_reads_previous_logs_only_for_restarted_containers():
    steady = container_status(name="steady", restarts=0)
    crashing = container_status(name="crashing", restarts=2)
    core = FakeCore(pods=[pod(statuses=[steady, crashing])], logs="fatal: API_KEY is not set\n")
    snap = collect(core, FakeApps(), "demo", now=T0)
    previous_reads = [
        c for c in core.calls if c[0] == "read_namespaced_pod_log" and c[3]["previous"]
    ]
    assert previous_reads == [
        (
            "read_namespaced_pod_log",
            "agent-abc",
            "demo",
            {
                "container": "crashing",
                "previous": True,
                "tail_lines": LOG_LINES,
                "since_seconds": LOG_SINCE_SECONDS,
                "timestamps": False,
            },
        )
    ]
    by_name = {c.name: c for c in snap.pods[0].containers}
    assert by_name["steady"].previous_logs == []
    assert by_name["crashing"].previous_logs == ["fatal: API_KEY is not set"]


def test_collect_keeps_at_most_the_last_log_lines():
    lines = [f"line {i}" for i in range(LOG_LINES + 15)]
    core = FakeCore(pods=[pod(statuses=[container_status(restarts=1)])], logs="\n".join(lines))
    logs = collect(core, FakeApps(), "demo", now=T0).pods[0].containers[0].previous_logs
    assert logs == lines[-LOG_LINES:]


def test_collect_tolerates_missing_previous_logs():
    core = FakeCore(
        pods=[pod(statuses=[container_status(restarts=1)])],
        log_error=RuntimeError("previous terminated container not found"),
    )
    snap = collect(core, FakeApps(), "demo", now=T0)
    assert snap.pods[0].containers[0].previous_logs == []


def test_collect_uses_only_read_calls():
    core = FakeCore(pods=[pod(statuses=[container_status(restarts=1)])], events=[event()], logs="x")
    apps = FakeApps(deployments=[deployment()])
    collect(core, apps, "demo", now=T0)
    assert {c[0] for c in core.calls} == ALLOWED
    assert {c[0] for c in apps.calls} == ALLOWED_APPS
    assert all(c[0].startswith(("list_", "read_")) for c in core.calls + apps.calls)


def test_event_falls_back_to_event_time_and_sorts_by_last_seen():
    late = event(reason="Late", when=T0 + timedelta(minutes=5))
    early = event(reason="Early", when=None)
    early.event_time = T0
    snap = collect(FakeCore(events=[late, early]), FakeApps(), "demo", now=T0)
    assert [e.reason for e in snap.events] == ["Early", "Late"]
    assert snap.events[0].first_seen == T0 and snap.events[0].last_seen == T0


def test_collect_fails_when_namespace_missing():
    core = FakeCore(pods=[pod()], namespaces=("demo",))
    with pytest.raises(NamespaceNotFound):
        collect(core, FakeApps(), "demoo", now=T0)
    assert [c[0] for c in core.calls] == ["read_namespace"]  # stopped before listing anything


def test_collect_passes_other_api_errors_through():
    class Forbidden(FakeCore):
        def read_namespace(self, name):
            raise ApiException(status=403, reason="Forbidden")

    with pytest.raises(ApiException):
        collect(Forbidden(), FakeApps(), "demo", now=T0)


TS_LINES = (
    "2026-11-02T10:00:00.123456789Z INFO upstream model ok\n"
    "2026-11-02T10:00:10.223456789Z ERROR upstream model call failed: 401 Unauthorized\n"
)


def test_collect_reads_recent_logs_for_every_container():
    a, b = container_status(name="a", restarts=0), container_status(name="b", restarts=3)
    core = FakeCore(pods=[pod(statuses=[a, b])], logs=TS_LINES)
    snap = collect(core, FakeApps(), "demo", now=T0)
    current = [c for c in core.calls if c[0] == "read_namespaced_pod_log" and not c[3]["previous"]]
    assert [c[3]["container"] for c in current] == ["a", "b"]
    for call in current:
        assert call[3]["tail_lines"] == LOG_LINES and call[3]["timestamps"] is True
    for c in snap.pods[0].containers:
        assert c.recent_logs == TS_LINES.splitlines()


def test_collect_tolerates_missing_recent_logs():
    core = FakeCore(pods=[pod()], log_error=RuntimeError("container not started"))
    (c,) = collect(core, FakeApps(), "demo", now=T0).pods[0].containers
    assert c.recent_logs == []


def test_recent_logs_keep_the_rfc3339_timestamp_prefix():
    core = FakeCore(pods=[pod()], logs=TS_LINES)
    (c,) = collect(core, FakeApps(), "demo", now=T0).pods[0].containers
    for line in c.recent_logs:
        timestamp, _, message = line.partition(" ")
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z", timestamp), line
        assert message


def test_log_reads_pass_line_and_time_limits():
    statuses = [container_status(name="a", restarts=2)]
    core = FakeCore(pods=[pod(statuses=statuses)], logs="x\n" * 100)
    snap = collect(core, FakeApps(), "demo", now=T0, log_lines=7, log_since_seconds=120)
    reads = [c[3] for c in core.calls if c[0] == "read_namespaced_pod_log"]
    assert len(reads) == 2  # previous and current run
    for kwargs in reads:
        assert kwargs["tail_lines"] == 7 and kwargs["since_seconds"] == 120
        assert "follow" not in kwargs
    (c,) = snap.pods[0].containers
    assert len(c.previous_logs) == 7 and len(c.recent_logs) == 7
    assert (snap.log_lines, snap.log_since_seconds) == (7, 120)


def test_collect_records_labels_and_passes_selector():
    core = FakeCore(pods=[pod(labels={"app": "agent", "tier": "ai"})])
    snap = collect(core, FakeApps(), "demo", now=T0, selector="app=agent")
    (listing,) = [c for c in core.calls if c[0] == "list_namespaced_pod"]
    assert listing[2] == {"label_selector": "app=agent"}
    assert snap.selector == "app=agent"
    assert snap.pods[0].labels == {"app": "agent", "tier": "ai"}


def _bad_request():
    import json

    error = ApiException(status=400, reason="Bad Request")
    error.body = json.dumps({"message": 'unable to parse requirement: invalid label key "bad!!"'})
    error.headers = {"Audit-Id": "abc-123"}
    return error


def test_collect_turns_a_rejected_selector_into_one_line():
    from tidewatch.collect import InvalidSelector

    core = FakeCore(list_error=_bad_request())
    with pytest.raises(InvalidSelector) as info:
        collect(core, FakeApps(), "demo", now=T0, selector="bad!!")
    assert str(info.value) == 'unable to parse requirement: invalid label key "bad!!"'
