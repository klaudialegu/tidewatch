"""Diagnosis rules. Each rule is a pure function rule(pod, snapshot) -> list[Finding].

Rules only read the snapshot (never the cluster), and never raise on missing optional data.
Restart-based rules are critical while the container is down, and a warning when it is running
and ready at snapshot time: Kubernetes keeps the last run's status for the pod's whole life, and
the snapshot can't tell a real recovery from a crash loop that happens to be up, so the warning
states facts and never calls the container healthy.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from tidewatch.findings import Finding, Severity
from tidewatch.snapshot import ContainerSpec, ContainerStatus, Pod, Snapshot

RuleFn = Callable[[Pod, Snapshot], list[Finding]]


@dataclass(frozen=True)
class Rule:
    id: str
    severity: Severity
    title: str
    fn: RuleFn


REGISTRY: dict[str, Rule] = {}


def rule(rule_id: str, severity: Severity, title: str) -> Callable[[RuleFn], RuleFn]:
    def register(fn: RuleFn) -> RuleFn:
        if rule_id in REGISTRY:
            raise ValueError(f"duplicate rule id: {rule_id}")
        REGISTRY[rule_id] = Rule(rule_id, severity, title, fn)
        return fn

    return register


# --- helpers ---------------------------------------------------------------------------------

PULL_REASONS = {"ErrImagePull", "ImagePullBackOff", "InvalidImageName"}
ERROR_LINE = re.compile(r"fatal|error|exception|traceback", re.IGNORECASE)
RS_HASH = re.compile(r"-[a-z0-9]{6,10}$")


def up_now(c: ContainerStatus) -> bool:
    return c.state.state == "running" and c.ready


def current_state(c: ContainerStatus) -> str:
    return c.state.state + (f" ({c.state.reason})" if c.state.reason else "")


def pick_log_lines(lines: list[str], limit: int = 3) -> list[str]:
    errors = [line for line in lines if ERROR_LINE.search(line)]
    return (errors or lines)[-limit:]


def container_spec(pod: Pod, snapshot: Snapshot, container: str) -> ContainerSpec | None:
    """The container's spec from the pod's Deployment (via its ReplicaSet owner), if any."""
    if not pod.owner or not pod.owner.startswith("ReplicaSet/"):
        return None
    deployment_name = RS_HASH.sub("", pod.owner.split("/", 1)[1])
    for deployment in snapshot.deployments:
        if deployment.name == deployment_name:
            for spec in deployment.containers:
                if spec.name == container:
                    return spec
    return None


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def restart_severity(c: ContainerStatus) -> Severity:
    return "warning" if up_now(c) else "critical"


# --- rules -----------------------------------------------------------------------------------


@rule("image-pull-failed", "critical", "Container image can't be pulled")
def image_pull_failed(pod: Pod, snapshot: Snapshot) -> list[Finding]:
    findings = []
    for c in pod.containers:
        if c.state.state != "waiting" or c.state.reason not in PULL_REASONS:
            continue
        evidence = [f"container {c.name} is waiting: {c.state.reason}", f"image: {c.image}"]
        failed = [
            e
            for e in snapshot.events
            if e.object == f"Pod/{pod.name}"
            and e.reason == "Failed"
            and "pull" in (e.message or "").lower()
        ]
        if failed:
            latest = failed[-1]
            evidence.append(f"event Failed (x{latest.count}): {latest.message}")
        findings.append(
            Finding(
                rule_id="image-pull-failed",
                severity="critical",
                title="Container image can't be pulled",
                object=f"Pod/{pod.name}",
                container=c.name,
                summary=f"Container {c.name} can't start: its image {c.image} can't be pulled.",
                evidence=evidence,
                next_steps=[
                    "Check the image name and tag in the Deployment for typos.",
                    "Confirm the image exists in the registry (or was loaded onto the node).",
                    "For a private registry, check imagePullSecrets on the pod or service account.",
                    "Check that the node can reach the registry (DNS, proxy, firewall).",
                ],
            )
        )
    return findings


@rule("container-exits-on-start", "critical", "Container keeps exiting")
def container_exits_on_start(pod: Pod, snapshot: Snapshot) -> list[Finding]:
    findings = []
    for c in pod.containers:
        last = c.last_state
        if not (
            c.restart_count >= 1
            and last is not None
            and last.state == "terminated"
            and last.exit_code not in (None, 0)
            and last.reason != "OOMKilled"
        ):
            continue
        code, n = last.exit_code, c.restart_count
        evidence = [
            f"last run: terminated, exit code {code}"
            + (f", reason {last.reason}" if last.reason else ""),
            f"restarts: {n}",
            f"current state: {current_state(c)}",
        ]
        if up_now(c) and last.reason == "Unknown":
            # Kubernetes records reason Unknown when the node or container runtime restarted
            # (for example the machine slept or rebooted), not when the app itself failed.
            findings.append(
                Finding(
                    rule_id="container-exits-on-start",
                    severity="info",
                    title="Container was restarted by a node or runtime restart",
                    object=f"Pod/{pod.name}",
                    container=c.name,
                    summary=(
                        f"Container {c.name} is running now; its last run ended with reason "
                        f"Unknown (exit code {code}), which Kubernetes records when the node or "
                        f"container runtime restarted rather than the app failing."
                    ),
                    evidence=evidence,
                    next_steps=[
                        "Check whether the node restarted: kubectl describe node <node>.",
                        "If it was a one-off (maintenance, sleep, reboot), no action is needed.",
                    ],
                )
            )
            continue
        severity = restart_severity(c)
        if severity == "critical":
            title = "Container keeps exiting"
            summary = f"Container {c.name} keeps exiting with code {code} ({plural(n, 'restart')})."
        else:
            title = "Container restarted after exiting"
            summary = (
                f"Container {c.name} is running now; its last run exited with code {code} "
                f"({plural(n, 'restart')})."
            )
        findings.append(
            Finding(
                rule_id="container-exits-on-start",
                severity=severity,
                title=title,
                object=f"Pod/{pod.name}",
                container=c.name,
                summary=summary,
                evidence=evidence,
                log_excerpt=pick_log_lines(c.previous_logs),
                next_steps=[
                    "Read the full previous logs: kubectl logs <pod> -c <container> --previous.",
                    "Check the config, environment variables and Secrets the app needs at startup.",
                    "Compare with the last working version (image tag, ConfigMaps, Secrets).",
                ],
            )
        )
    return findings


@rule("oom-killed", "critical", "Container killed for exceeding its memory limit")
def oom_killed(pod: Pod, snapshot: Snapshot) -> list[Finding]:
    findings = []
    for c in pod.containers:
        runs = [s for s in (c.state, c.last_state) if s is not None]
        oom = next((s for s in runs if s.state == "terminated" and s.reason == "OOMKilled"), None)
        if oom is None:
            continue
        spec = container_spec(pod, snapshot, c.name)
        limit = (spec.memory_limit if spec else None) or "unknown"
        severity = restart_severity(c)
        if severity == "critical":
            summary = (
                f"Container {c.name} was killed for using more memory than its limit ({limit})."
            )
        else:
            summary = (
                f"Container {c.name} is running now; its last run was killed for using more "
                f"memory than its limit ({limit}), {plural(c.restart_count, 'restart')}."
            )
        code = f" (exit code {oom.exit_code})" if oom.exit_code is not None else ""
        findings.append(
            Finding(
                rule_id="oom-killed",
                severity=severity,
                title="Container killed for exceeding its memory limit",
                object=f"Pod/{pod.name}",
                container=c.name,
                summary=summary,
                evidence=[
                    f"terminated: OOMKilled{code}",
                    f"restarts: {c.restart_count}",
                    f"memory limit: {limit}",
                    f"current state: {current_state(c)}",
                ],
                next_steps=[
                    "Compare the container's real memory use with its limit (kubectl top pod).",
                    "If the app needs more, raise resources.limits.memory in the Deployment.",
                    "If use keeps growing until it's killed, look for a memory leak.",
                ],
            )
        )
    return findings
