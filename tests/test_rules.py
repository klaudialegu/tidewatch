import pytest
from snapshots import (
    cstatus,
    mkdeployment,
    mkevent,
    mkpod,
    mksnapshot,
    terminated,
    waiting,
)

from tidewatch.rules import (
    REGISTRY,
    container_exits_on_start,
    image_pull_failed,
    oom_killed,
    rule,
)

ALL_RULES = [image_pull_failed, container_exits_on_start, oom_killed]


def run(fn, pod, **snap):
    return fn(pod, mksnapshot(pods=[pod], **snap))


def test_registry_rejects_duplicate_rule_ids():
    with pytest.raises(ValueError, match="duplicate rule id: image-pull-failed"):

        @rule("image-pull-failed", "critical", "again")
        def _again(pod, snapshot):
            return []

    assert REGISTRY["image-pull-failed"].fn is image_pull_failed


def test_every_rule_has_id_severity_and_title():
    assert set(REGISTRY) >= {"image-pull-failed", "container-exits-on-start", "oom-killed"}
    for rule_id, r in REGISTRY.items():
        assert r.id == rule_id and r.title
        assert r.severity in {"critical", "warning", "info"}


def test_image_pull_rule_reports_reason_image_and_event():
    pod = mkpod(
        containers=[
            cstatus(image="app:0.0.0-missing", ready=False, state=waiting("ImagePullBackOff"))
        ]
    )
    events = [
        mkevent("Scheduled", f"Pod/{pod.name}", "assigned", typ="Normal"),
        mkevent("Failed", f"Pod/{pod.name}", 'Failed to pull image "app:0.0.0-missing"', count=2),
        mkevent("Failed", "Pod/other-pod", "Failed to pull image other"),
    ]
    (f,) = run(image_pull_failed, pod, events=events)
    assert (f.rule_id, f.severity, f.object, f.container) == (
        "image-pull-failed",
        "critical",
        f"Pod/{pod.name}",
        "agent",
    )
    assert "container agent is waiting: ImagePullBackOff" in f.evidence
    assert "image: app:0.0.0-missing" in f.evidence
    assert 'event Failed (x2): Failed to pull image "app:0.0.0-missing"' in f.evidence
    assert not any("other" in e for e in f.evidence)
    assert f.next_steps


def test_exits_on_start_rule_prefers_error_log_lines():
    logs = [
        "starting",
        "loading config",
        "Traceback (most recent call last):",
        "line a",
        "ValueError: bad",
        "fatal: API_KEY is not set",
        "bye",
    ]
    pod = mkpod(
        containers=[
            cstatus(
                ready=False,
                restarts=3,
                state=waiting("CrashLoopBackOff"),
                last=terminated("Error", 1),
                logs=logs,
            )
        ]
    )
    (f,) = run(container_exits_on_start, pod)
    assert f.severity == "critical" and f.title == "Container keeps exiting"
    assert "with code 1" in f.summary and "3 restarts" in f.summary
    assert f.log_excerpt == [
        "Traceback (most recent call last):",
        "ValueError: bad",
        "fatal: API_KEY is not set",
    ]
    no_errors = mkpod(
        containers=[
            cstatus(
                ready=False,
                restarts=1,
                state=waiting("CrashLoopBackOff"),
                last=terminated("Error", 2),
                logs=["a", "b", "c", "d"],
            )
        ]
    )
    assert run(container_exits_on_start, no_errors)[0].log_excerpt == ["b", "c", "d"]


def test_exits_on_start_rule_ignores_oom_and_zero_exit():
    oom = mkpod(
        containers=[
            cstatus(
                ready=False,
                restarts=2,
                state=waiting("CrashLoopBackOff"),
                last=terminated("OOMKilled", 137),
            )
        ]
    )
    clean = mkpod(containers=[cstatus(restarts=1, last=terminated("Completed", 0))])
    assert run(container_exits_on_start, oom) == []
    assert run(container_exits_on_start, clean) == []


def test_recovered_running_container_is_warning_not_critical():
    exited = mkpod(containers=[cstatus(restarts=1, last=terminated("Error", 1))])
    (f,) = run(container_exits_on_start, exited)
    assert f.severity == "warning" and f.title == "Container restarted after exiting"
    assert f.summary == (
        "Container agent is running now; its last run exited with code 1 (1 restart)."
    )
    oomed = mkpod(containers=[cstatus(restarts=2, last=terminated("OOMKilled", 137))])
    (g,) = run(oom_killed, oomed, deployments=[mkdeployment()])
    assert g.severity == "warning" and "is running now" in g.summary
    for finding in (f, g):
        assert "healthy" not in (finding.summary + finding.title).lower()


def test_unknown_exit_on_running_container_is_info_node_restart():
    pod = mkpod(containers=[cstatus(restarts=1, last=terminated("Unknown", 255))])
    (f,) = run(container_exits_on_start, pod)
    assert f.severity == "info"
    assert f.title == "Container was restarted by a node or runtime restart"
    down = mkpod(
        containers=[
            cstatus(
                ready=False,
                restarts=4,
                state=waiting("CrashLoopBackOff"),
                last=terminated("Unknown", 255),
            )
        ]
    )
    assert run(container_exits_on_start, down)[0].severity == "critical"


def test_oom_rule_reports_memory_limit_from_deployment():
    pod = mkpod(
        containers=[
            cstatus(
                ready=False,
                restarts=3,
                state=waiting("CrashLoopBackOff"),
                last=terminated("OOMKilled", 137),
            )
        ]
    )
    (f,) = run(
        oom_killed,
        pod,
        deployments=[
            mkdeployment(memory_limit="64Mi"),
            mkdeployment(name="other", memory_limit="1Gi"),
        ],
    )
    assert f.severity == "critical"
    assert "memory limit: 64Mi" in f.evidence
    assert "terminated: OOMKilled (exit code 137)" in f.evidence
    assert "(64Mi)" in f.summary


def test_oom_rule_without_matching_deployment_still_reports():
    last = terminated("OOMKilled", 137)
    for owner in (None, "StatefulSet/db", "ReplicaSet/unrelated-5d8f7c9b6a"):
        pod = mkpod(
            owner=owner,
            containers=[
                cstatus(ready=False, restarts=1, state=waiting("CrashLoopBackOff"), last=last)
            ],
        )
        (f,) = run(oom_killed, pod, deployments=[mkdeployment()])
        assert "memory limit: unknown" in f.evidence, owner


def test_evidence_never_contains_log_lines():
    logs = ["fatal: API_KEY is not set", "error: something"]
    pod = mkpod(
        containers=[
            cstatus(
                ready=False,
                restarts=2,
                state=waiting("CrashLoopBackOff"),
                last=terminated("Error", 1),
                logs=logs,
            )
        ]
    )
    (f,) = run(container_exits_on_start, pod)
    assert f.log_excerpt
    assert not any(line in e for e in f.evidence for line in logs)


def test_rules_ignore_a_healthy_running_container():
    pod = mkpod()
    snap_kwargs = {"deployments": [mkdeployment()]}
    for fn in ALL_RULES:
        assert run(fn, pod, **snap_kwargs) == [], fn.__name__
