from pathlib import Path

from fakes import T0
from snapshots import cstatus, mkpod, mksnapshot, terminated, waiting

from tidewatch.diagnose import diagnose
from tidewatch.findings import MAX_EVIDENCE, Finding, Report
from tidewatch.rules import REGISTRY, Rule, llm_upstream_auth_failed
from tidewatch.snapshot import load

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture_report(name: str) -> Report:
    return diagnose(load(FIXTURES / f"{name}.json"), now=T0)


def rule_ids(report: Report) -> list[str]:
    return [f.rule_id for f in report.findings]


def test_healthy_fixture_has_no_findings():
    report = fixture_report("healthy")
    assert report.findings == []
    assert report.counts == {"critical": 0, "warning": 0, "info": 0}


def test_bad_image_fixture_yields_only_image_pull_failed():
    report = fixture_report("bad-image")
    assert rule_ids(report) == ["image-pull-failed"]
    assert report.findings[0].severity == "critical"
    assert any("0.0.0-missing" in e for e in report.findings[0].evidence)


def test_no_key_fixture_yields_only_exits_on_start_with_log_evidence():
    report = fixture_report("no-key")
    assert rule_ids(report) == ["container-exits-on-start"]
    (f,) = report.findings
    assert f.severity == "critical"
    assert "fatal: API_KEY is not set" in f.log_excerpt


def test_memory_fixture_yields_only_oom_killed_with_limit():
    report = fixture_report("memory")
    assert rule_ids(report) == ["oom-killed"]
    assert "memory limit: 64Mi" in report.findings[0].evidence


def _finding(severity, obj, rule_id="x"):
    return Finding(rule_id=rule_id, severity=severity, title="t", object=obj, summary="s")


def test_findings_sorted_by_severity_then_object():
    canned = [
        _finding("info", "Pod/a"),
        _finding("warning", "Pod/b"),
        _finding("critical", "Pod/z"),
        _finding("critical", "Pod/c"),
    ]
    fake = Rule("canned", "critical", "canned", lambda pod, snap: canned)
    report = diagnose(mksnapshot(pods=[mkpod()]), rules=[fake], now=T0)
    assert [(f.severity, f.object) for f in report.findings] == [
        ("critical", "Pod/c"),
        ("critical", "Pod/z"),
        ("warning", "Pod/b"),
        ("info", "Pod/a"),
    ]


def test_rule_error_becomes_info_finding_not_crash():
    def boom(pod, snap):
        raise KeyError("missing")

    report = diagnose(
        mksnapshot(pods=[mkpod()]), rules=[Rule("boom", "critical", "b", boom)], now=T0
    )
    (f,) = report.findings
    assert (f.rule_id, f.severity, f.object) == ("rule-error", "info", "Pod/agent-7c8b696666-abcde")
    assert "boom" in f.summary and "KeyError" in f.summary


def test_rule_error_on_one_pod_keeps_findings_for_other_pods():
    def picky(pod, snap):
        if pod.name == "bad":
            raise RuntimeError("weird pod")
        return [_finding("critical", f"Pod/{pod.name}", "picky")]

    pods = [mkpod(name="good-1"), mkpod(name="bad"), mkpod(name="good-2")]
    report = diagnose(mksnapshot(pods=pods), rules=[Rule("picky", "critical", "p", picky)], now=T0)
    assert sorted((f.rule_id, f.object) for f in report.findings) == [
        ("picky", "Pod/good-1"),
        ("picky", "Pod/good-2"),
        ("rule-error", "Pod/bad"),
    ]


def test_report_counts_match_findings():
    pods = [
        mkpod(name="p1", containers=[cstatus(ready=False, state=waiting("ErrImagePull"))]),
        mkpod(name="p2", containers=[cstatus(restarts=1, last=terminated("Error", 1))]),
        mkpod(name="p3", containers=[cstatus(restarts=1, last=terminated("Unknown", 255))]),
    ]
    report = diagnose(mksnapshot(pods=pods), now=T0)
    assert report.counts == {"critical": 1, "warning": 1, "info": 1}
    for severity, n in report.counts.items():
        assert n == sum(f.severity == severity for f in report.findings)


def test_report_json_round_trip_and_schema_version():
    report = fixture_report("no-key")
    again = Report.model_validate_json(report.model_dump_json())
    assert again == report
    assert again.schema_version == "1"
    assert (again.context, again.namespace) == ("kind-tidewatch-lab", "demo")


def test_evidence_is_capped_at_five_items():
    f = Finding(
        rule_id="x",
        severity="info",
        title="t",
        object="Pod/p",
        summary="s",
        evidence=[str(i) for i in range(MAX_EVIDENCE + 3)],
        log_excerpt=["a", "b", "c", "d", "e"],
    )
    assert f.evidence == [str(i) for i in range(MAX_EVIDENCE)]
    assert f.log_excerpt == ["a", "b", "c"]


def only_finding_on_app(name: str, app: str = "agent"):
    snapshot = load(FIXTURES / f"{name}.json")
    report = diagnose(snapshot, now=T0)
    assert len(report.findings) == 1, [(f.rule_id, f.object) for f in report.findings]
    (f,) = report.findings
    assert f.rule_id in REGISTRY, f.rule_id  # the finding's id is the id the rule registered
    labels = {f"Pod/{p.name}": p.labels for p in snapshot.pods}
    assert labels[f.object].get("app") == app  # by label, never by pod name
    return f


def test_llm_bad_key_fixture_yields_only_llm_upstream_auth_failed():
    f = only_finding_on_app("llm-bad-key")
    assert (f.rule_id, f.severity) == ("llm-upstream-auth-failed", "critical")
    assert REGISTRY[f.rule_id].fn is llm_upstream_auth_failed
    assert "running and ready" in f.summary


def test_llm_rate_limit_fixture_yields_only_llm_upstream_rate_limited():
    f = only_finding_on_app("llm-rate-limit")
    assert (f.rule_id, f.severity) == ("llm-upstream-rate-limited", "warning")


def test_llm_timeout_fixture_yields_only_llm_upstream_timeout():
    f = only_finding_on_app("llm-timeout")
    assert (f.rule_id, f.severity) == ("llm-upstream-timeout", "critical")


def test_existing_scenario_fixtures_unchanged_findings():
    expected = {
        "bad-image": "image-pull-failed",
        "no-key": "container-exits-on-start",
        "memory": "oom-killed",
    }
    for name, rule_id in expected.items():
        assert only_finding_on_app(name).rule_id == rule_id, name
