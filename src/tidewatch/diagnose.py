"""Run every rule against a snapshot and build the report."""

from collections.abc import Iterable
from datetime import UTC, datetime

from tidewatch import rules as _rules  # noqa: F401  (registers the built-in rules)
from tidewatch.findings import SEVERITY_RANK, Finding, Report
from tidewatch.rules import REGISTRY, Rule
from tidewatch.snapshot import Snapshot


def diagnose(
    snapshot: Snapshot, rules: Iterable[Rule] | None = None, now: datetime | None = None
) -> Report:
    active = list(REGISTRY.values()) if rules is None else list(rules)
    findings: list[Finding] = []
    for pod in snapshot.pods:
        for r in active:
            try:
                findings.extend(r.fn(pod, snapshot))
            except Exception as exc:  # one bad pod must not hide this rule's other findings
                findings.append(
                    Finding(
                        rule_id="rule-error",
                        severity="info",
                        title=f"Rule {r.id} failed on this pod",
                        object=f"Pod/{pod.name}",
                        summary=f"Rule {r.id} raised {type(exc).__name__}: {exc}",
                        next_steps=["This is a Tidewatch bug: report it with the snapshot."],
                    )
                )
    findings.sort(key=lambda f: (SEVERITY_RANK[f.severity], f.object, f.rule_id))
    counts = {s: sum(f.severity == s for f in findings) for s in SEVERITY_RANK}
    return Report(
        generated_at=now or datetime.now(UTC),
        context=snapshot.context,
        namespace=snapshot.namespace,
        snapshot_collected_at=snapshot.collected_at,
        counts=counts,
        findings=findings,
    )
