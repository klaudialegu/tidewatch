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
    assert run(container_exits_on_start, no_errors)[0].log_excerpt == []  # matched lines only


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


# --- LLM provider rules ----------------------------------------------------------------------

from tidewatch.findings import mask_secrets  # noqa: E402
from tidewatch.rules import (  # noqa: E402
    llm_upstream_auth_failed,
    llm_upstream_rate_limited,
    llm_upstream_timeout,
)

LLM_RULES = [llm_upstream_auth_failed, llm_upstream_rate_limited, llm_upstream_timeout]
TS = "2026-11-03T02:40:{:02d}.000000000Z"


def agent_with(*messages, ready=True, restarts=0):
    lines = [f"{TS.format(i)} {m}" for i, m in enumerate(messages)]
    container = cstatus(ready=ready, restarts=restarts)
    container.recent_logs = lines
    return mkpod(containers=[container])


def test_llm_auth_rule_matches_common_wordings():
    for message in (
        "ERROR upstream model call failed: 401 Unauthorized (invalid API key)",
        "openai-style API error: Error code: 401 - Incorrect API key provided",
        "provider AuthenticationError: invalid api key",
        "llm call failed: status code 403 permission denied",
        # the key wording alone, with no status code, "Unauthorized" or other auth word
        "upstream model call failed: invalid API key",
        "provider rejected the request: Incorrect API key provided",
    ):
        (f,) = run(llm_upstream_auth_failed, agent_with(message))
        assert (f.rule_id, f.severity) == ("llm-upstream-auth-failed", "critical"), message


def test_llm_rate_limit_rule_is_warning_and_matches_429():
    for message in (
        "ERROR upstream model call failed: 429 Too Many Requests (retry after 30s)",
        "model API rate limit exceeded, retrying",
    ):
        (f,) = run(llm_upstream_rate_limited, agent_with(message))
        assert f.severity == "warning", message
    assert run(llm_upstream_auth_failed, agent_with("upstream model call failed: 429")) == []


def test_llm_timeout_rule_needs_an_upstream_word():
    (f,) = run(
        llm_upstream_timeout, agent_with("ERROR upstream model call failed: timed out after 5s")
    )
    assert f.severity == "critical"
    assert run(llm_upstream_timeout, agent_with("db connection timeout after 30s")) == []


def test_llm_rules_ignore_ok_lines_and_empty_logs():
    healthy = agent_with("INFO upstream model ok", "INFO upstream model ok")
    startup = agent_with(
        "ERROR upstream model call failed: connection failed: refused", "INFO upstream model ok"
    )
    empty = mkpod()
    for fn in LLM_RULES:
        for pod in (healthy, startup, empty):
            assert run(fn, pod) == [], fn.__name__


def test_llm_rule_evidence_says_container_is_running_and_ready():
    pod = agent_with(*["ERROR upstream model call failed: 401 Unauthorized"] * 3)
    (f,) = run(llm_upstream_auth_failed, pod)
    assert "container agent: running, ready=true, 0 restarts" in f.evidence
    assert any(e.startswith("3 of 3 recent log lines") for e in f.evidence)
    assert f"first seen {TS.format(0)}, last seen {TS.format(2)}" in f.evidence
    assert "while Kubernetes shows it running and ready" in f.summary


def test_llm_rules_ignore_access_log_lines_with_401_or_429_in_ports():
    pod = agent_with(
        'INFO:     10.244.0.1:40123 - "GET /health HTTP/1.1" 200 OK',
        'INFO:     10.244.0.1:54291 - "POST /v1/chat HTTP/1.1" 200 OK',
        'INFO:     10.244.0.1:4291 - "POST /api/chat HTTP/1.1" 401 Unauthorized',
        "INFO upstream model ok",
    )
    for fn in LLM_RULES:
        assert run(fn, pod) == [], fn.__name__


def test_llm_status_codes_in_upstream_ports_are_not_status_codes():
    pod = agent_with(
        "ERROR upstream model call failed: connection refused by model.demo.svc:8401",
        "ERROR upstream model call failed: connection refused by 10.96.0.12:9429",
    )
    assert run(llm_upstream_auth_failed, pod) == []
    assert run(llm_upstream_rate_limited, pod) == []


def test_llm_rule_downgrades_when_latest_upstream_line_is_ok():
    pod = agent_with(
        "ERROR upstream model call failed: 401 Unauthorized",
        "ERROR upstream model call failed: 401 Unauthorized",
        "INFO upstream model ok",
    )
    (f,) = run(llm_upstream_auth_failed, pod)
    assert f.severity == "warning"
    assert f"latest upstream call succeeded at {TS.format(2)}" in f.evidence
    assert "healthy" not in (f.summary + f.title).lower()
    rate = agent_with("upstream model call failed: 429 Too Many Requests", "INFO upstream model ok")
    assert run(llm_upstream_rate_limited, rate)[0].severity == "info"


def test_llm_auth_excerpt_masks_key_like_tokens():
    pod = agent_with(
        "ERROR upstream model call failed: 401 Incorrect API key provided: sk-abc123SECRET99",
        "ERROR upstream model call failed: 401 Unauthorized Bearer eyJhbGciOi.xyz",
    )
    (f,) = run(llm_upstream_auth_failed, pod)
    joined = " ".join(f.log_excerpt)
    assert "sk-***" in joined and "Bearer ***" in joined
    assert "abc123SECRET99" not in joined and "eyJhbGciOi" not in joined
    assert mask_secrets(TS.format(1) + " INFO ok") == TS.format(1) + " INFO ok"


def test_excerpts_hold_only_matched_lines():
    pod = agent_with(
        "INFO starting",
        "ERROR upstream model call failed: 401 Unauthorized",
        "INFO upstream model ok",
        "INFO serving",
        "ERROR upstream model call failed: 401 Unauthorized",
    )
    (f,) = run(llm_upstream_auth_failed, pod)
    assert len(f.log_excerpt) == 2
    assert all("401 Unauthorized" in line for line in f.log_excerpt)
    crash = mkpod(
        containers=[
            cstatus(
                ready=False,
                restarts=1,
                state=waiting("CrashLoopBackOff"),
                last=terminated("Error", 1),
                logs=["boot", "fatal: missing config", "bye"],
            )
        ]
    )
    assert run(container_exits_on_start, crash)[0].log_excerpt == ["fatal: missing config"]
    quiet = mkpod(
        containers=[
            cstatus(
                ready=False,
                restarts=1,
                state=waiting("CrashLoopBackOff"),
                last=terminated("Error", 1),
                logs=["boot", "loading", "bye"],
            )
        ]
    )
    assert run(container_exits_on_start, quiet)[0].log_excerpt == []  # nothing matched
