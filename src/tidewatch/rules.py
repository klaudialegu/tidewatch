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
    """Only lines that matched (never the lines around them); none matched means none."""
    return [line for line in lines if ERROR_LINE.search(line)][-limit:]


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


# --- LLM provider (upstream) rules ------------------------------------------------------------
# These read a container's current-run logs. They never look at pod names: a pod gets a finding
# only from its own log lines (ADR 0004). Only lines about an upstream call count, web-server
# access-log lines never do, and status codes only match in a status position, so a client port
# like 54291 can't look like a 429.

TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$")
ACCESS_LOG = re.compile(r'"(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) [^"]* HTTP/\d(?:\.\d)?"')
UPSTREAM = re.compile(r"\b(?:upstream|model|llm|api|provider|completions?)\b", re.IGNORECASE)
STATUS = r"(?:status(?:\s+code)?|http|error\s+code|failed:)\s*[:=]?\s*"
FAILURE = re.compile(r"\b(?:fail(?:ed|ure)?|error|exception|denied)\b", re.IGNORECASE)
SUCCESS = re.compile(r"\b(?:ok|succeeded|success)\b", re.IGNORECASE)
AUTH = re.compile(
    STATUS + r"40[13]\b|\bunauthori[sz]ed\b|\b(?:invalid|incorrect)\s+api\s+key\b"
    r"|\bauthentication(?:error|\s+failed)\b|\bpermission\s+denied\b",
    re.IGNORECASE,
)
RATE = re.compile(
    STATUS + r"429\b|\brate[\s_-]?limit(?:ed|s)?\b|\btoo\s+many\s+requests\b", re.IGNORECASE
)
TIMEOUT = re.compile(r"\btimed\s+out\b|\btimeout\b|\bdeadline\s+exceeded\b", re.IGNORECASE)
# Rate-limit and timeout words also show up in harmless config lines ("timeout=30s, rate limit
# 60/min, log_level=error"). For those two rules a hit followed by a number is a setting, not an
# event, and the line must also say a call went wrong (a failure word or a 4xx/5xx status).
SETTING_VALUE = re.compile(r"\s*[=:]?\s*\d")
FAILED_CALL = re.compile(
    FAILURE.pattern
    + r"|\b(?:exceeded|timed\s+out|too\s+many\s+requests|rate[\s_-]?limited|throttled)\b"
    + r"|"
    + STATUS
    + r"[45]\d\d\b",
    re.IGNORECASE,
)


def reports_event(pattern: re.Pattern, message: str) -> bool:
    """True if a hit of `pattern` is an event, not a setting like `timeout=30s`."""
    return any(not SETTING_VALUE.match(message, hit.end()) for hit in pattern.finditer(message))


def is_hit(pattern: re.Pattern, message: str, needs_failure: bool) -> bool:
    if not needs_failure:
        return bool(pattern.search(message))
    return reports_event(pattern, message) and bool(FAILED_CALL.search(message))


@dataclass(frozen=True)
class LogLine:
    raw: str
    timestamp: str | None
    message: str


def parse_log(lines: list[str]) -> list[LogLine]:
    parsed = []
    for raw in lines:
        first, _, rest = raw.partition(" ")
        if TIMESTAMP.match(first):
            parsed.append(LogLine(raw, first, rest))
        else:
            parsed.append(LogLine(raw, None, raw))
    return parsed


def upstream_lines(c: ContainerStatus) -> list[LogLine]:
    return [
        line
        for line in parse_log(c.recent_logs)
        if UPSTREAM.search(line.message) and not ACCESS_LOG.search(line.message)
    ]


def is_success(line: LogLine) -> bool:
    return bool(SUCCESS.search(line.message)) and not FAILURE.search(line.message)


def window_text(snapshot: Snapshot) -> str:
    parts = []
    if snapshot.log_lines:
        parts.append(f"last {snapshot.log_lines} lines")
    if snapshot.log_since_seconds:
        parts.append(f"last {snapshot.log_since_seconds // 60} min")
    return " / ".join(parts) or "recent logs"


def llm_findings(
    pod: Pod,
    snapshot: Snapshot,
    *,
    rule_id: str,
    pattern: re.Pattern,
    severity: Severity,
    title: str,
    what: str,
    next_steps: list[str],
    needs_failure: bool = False,
    select: Callable[[ContainerStatus], list[LogLine]] | None = None,
    skip: Callable[[str], bool] | None = None,
) -> list[Finding]:
    """`select` picks the lines a rule reads (default: upstream lines); a line `skip` claims
    belongs to a more specific rule, so one log line never gives two findings."""
    findings = []
    for c in pod.containers:
        upstream = (select or upstream_lines)(c)
        matched = [
            line
            for line in upstream
            if is_hit(pattern, line.message, needs_failure) and not (skip and skip(line.message))
        ]
        if not matched:
            continue
        latest = upstream[-1]
        recovered = latest not in matched and is_success(latest)
        level: Severity = severity
        if recovered:
            level = "warning" if severity == "critical" else "info"
        evidence = [
            f"{len(matched)} of {len(c.recent_logs)} recent log lines ({window_text(snapshot)}) "
            f"show {what}",
            f"container {c.name}: {current_state(c)}, ready={str(c.ready).lower()}, "
            f"{plural(c.restart_count, 'restart')}",
        ]
        if matched[0].timestamp:
            evidence.append(f"first seen {matched[0].timestamp}, last seen {matched[-1].timestamp}")
        if recovered:
            when = f" at {latest.timestamp}" if latest.timestamp else ""
            evidence.append(f"latest upstream call succeeded{when}")
            summary = (
                f"Container {c.name}'s latest upstream call succeeded; {len(matched)} earlier "
                f"log lines show {what}."
            )
        else:
            green = " while Kubernetes shows it running and ready" if up_now(c) else ""
            summary = f"Container {c.name} logs {what}{green}."
        findings.append(
            Finding(
                rule_id=rule_id,
                severity=level,
                title=title,
                object=f"Pod/{pod.name}",
                container=c.name,
                summary=summary,
                evidence=evidence,
                log_excerpt=[line.raw for line in matched[-3:]],
                next_steps=next_steps,
            )
        )
    return findings


@rule("llm-upstream-auth-failed", "critical", "LLM provider rejects the credentials")
def llm_upstream_auth_failed(pod: Pod, snapshot: Snapshot) -> list[Finding]:
    return llm_findings(
        pod,
        snapshot,
        rule_id="llm-upstream-auth-failed",
        skip=elevenlabs_line,
        pattern=AUTH,
        severity="critical",
        title="LLM provider rejects the credentials",
        what="the model provider rejecting the credentials (401/403)",
        next_steps=[
            "Check the Secret holding the provider key: right key, right environment, not empty.",
            "Check whether the key was rotated or revoked on the provider side.",
            "After fixing the Secret, restart the Deployment so pods read the new value.",
        ],
    )


@rule("llm-upstream-rate-limited", "warning", "LLM provider is rate-limiting")
def llm_upstream_rate_limited(pod: Pod, snapshot: Snapshot) -> list[Finding]:
    return llm_findings(
        pod,
        snapshot,
        rule_id="llm-upstream-rate-limited",
        skip=elevenlabs_line,
        pattern=RATE,
        severity="warning",
        title="LLM provider is rate-limiting",
        what="the model provider rate-limiting requests (429)",
        needs_failure=True,
        next_steps=[
            "Check the provider account's quota and current usage.",
            "Make sure the app backs off and honours Retry-After instead of retrying at once.",
            "Check for a traffic spike or a retry loop multiplying requests.",
        ],
    )


@rule("llm-upstream-timeout", "critical", "LLM provider calls time out")
def llm_upstream_timeout(pod: Pod, snapshot: Snapshot) -> list[Finding]:
    return llm_findings(
        pod,
        snapshot,
        rule_id="llm-upstream-timeout",
        skip=elevenlabs_line,
        pattern=TIMEOUT,
        severity="critical",
        title="LLM provider calls time out",
        what="calls to the model provider timing out",
        needs_failure=True,
        next_steps=[
            "Check egress from the cluster to the provider (DNS, proxy, NetworkPolicy, firewall).",
            "Check the provider's status page for latency or an outage.",
            "Compare the app's timeout with the provider's typical latency for this model.",
        ],
    )


# --- ElevenLabs API rules -----------------------------------------------------------------------
# Only error codes ElevenLabs documents (read 2026-11-17):
#   E: https://elevenlabs.io/docs/eleven-api/resources/errors
#   H: https://elevenlabs.io/docs/help-center/technical/api-error-code-400-or-401
#   C: https://elevenlabs.io/docs/changelog/2025/5/5
# Errors come back as {"detail": {"type", "code", "message", "status" (legacy), "request_id"}}
# (E, H). A code counts only as the value of a code or status field ("code": "x", 'status': 'x',
# code=x), so a config line like `retry_on=rate_limit_exceeded` or prose naming a code never
# matches. A line one of these rules matches is left out of the generic LLM rules (ADR 0006).


def documented_code(*codes: str) -> str:
    # `\?`: a body logged as a string inside a JSON log line has escaped quotes (\"code\").
    q = r"\\?[\"']?"
    return (
        r"(?<![\w-])"
        + q
        + r"(?:code|status)"
        + q
        + r"\s*[:=]\s*"
        + q
        + r"(?:"
        + "|".join(codes)
        + r")\b"
    )


EL_AUTH = re.compile(documented_code("invalid_api_key", "missing_api_key"), re.IGNORECASE)
EL_QUOTA = re.compile(
    documented_code("insufficient_credits", "quota_exceeded")
    + r"|\bexceeds\s+your\s+quota\s+limit\b",
    re.IGNORECASE,
)
EL_RATE = re.compile(
    documented_code("rate_limit_exceeded", "concurrent_limit_exceeded", "system_busy"),
    re.IGNORECASE,
)
EL_VOICE = re.compile(documented_code("voice_not_found", "invalid_voice_id"), re.IGNORECASE)
EL_PATTERNS = (EL_AUTH, EL_QUOTA, EL_RATE, EL_VOICE)


def elevenlabs_line(message: str) -> bool:
    """True if one of the ElevenLabs rules matches this line."""
    return any(p.search(message) for p in EL_PATTERNS)


def provider_lines(c: ContainerStatus) -> list[LogLine]:
    """Upstream lines plus lines holding a documented ElevenLabs error (an error body alone
    may have no upstream word); never web-server access-log lines."""
    return [
        line
        for line in parse_log(c.recent_logs)
        if (UPSTREAM.search(line.message) or elevenlabs_line(line.message))
        and not ACCESS_LOG.search(line.message)
    ]


SUPPORT_STEP = "If it persists, contact the provider's support with the request_id from the error."


@rule("elevenlabs-auth-failed", "critical", "ElevenLabs rejects the API key")
def elevenlabs_auth_failed(pod: Pod, snapshot: Snapshot) -> list[Finding]:
    """401 invalid_api_key / missing_api_key (E; H). The key goes in the `xi-api-key` header
    (https://elevenlabs.io/docs/api-reference/authentication)."""
    return llm_findings(
        pod,
        snapshot,
        rule_id="elevenlabs-auth-failed",
        pattern=EL_AUTH,
        severity="critical",
        title="ElevenLabs rejects the API key",
        what="the provider rejecting the API key as invalid or missing (401)",
        select=provider_lines,
        next_steps=[
            "Check the Secret holding the provider key: right key, right environment, not empty.",
            "Check the app sends it in the `xi-api-key` header, spelled exactly that way.",
            "After fixing the Secret, restart the Deployment so pods read the new value.",
        ],
    )


@rule("elevenlabs-quota-exceeded", "critical", "ElevenLabs quota or credits exhausted")
def elevenlabs_quota_exceeded(pod: Pod, snapshot: Snapshot) -> list[Finding]:
    """402 insufficient_credits (E), quota_exceeded (H), and the 401 "This request exceeds your
    quota limit" message (C)."""
    return llm_findings(
        pod,
        snapshot,
        rule_id="elevenlabs-quota-exceeded",
        pattern=EL_QUOTA,
        severity="critical",
        title="ElevenLabs quota or credits exhausted",
        what="the provider refusing requests for lack of credits or quota",
        select=provider_lines,
        next_steps=[
            "Check the account's remaining credits and quota with the provider.",
            "Add credits or enable usage-based billing if the plan allows it.",
            "Check for a usage spike or a retry loop burning credits.",
            SUPPORT_STEP,
        ],
    )


@rule("elevenlabs-rate-limited", "warning", "ElevenLabs is rate-limiting")
def elevenlabs_rate_limited(pod: Pod, snapshot: Snapshot) -> list[Finding]:
    """429 rate_limit_exceeded, concurrent_limit_exceeded, system_busy (E)."""
    return llm_findings(
        pod,
        snapshot,
        rule_id="elevenlabs-rate-limited",
        pattern=EL_RATE,
        severity="warning",
        title="ElevenLabs is rate-limiting",
        what="the provider rate-limiting requests (429)",
        select=provider_lines,
        next_steps=[
            "Make sure the app retries 429s with exponential backoff, not at once.",
            "concurrent_limit_exceeded: compare the app's parallel requests with the plan's "
            "concurrency limit.",
            "system_busy is on the provider's side: back off and retry later.",
            SUPPORT_STEP,
        ],
    )


@rule("elevenlabs-voice-not-found", "critical", "ElevenLabs voice ID not found")
def elevenlabs_voice_not_found(pod: Pod, snapshot: Snapshot) -> list[Finding]:
    """404 voice_not_found, 400 invalid_voice_id (E; H)."""
    return llm_findings(
        pod,
        snapshot,
        rule_id="elevenlabs-voice-not-found",
        pattern=EL_VOICE,
        severity="critical",
        title="ElevenLabs voice ID not found",
        what="the provider rejecting the voice ID as unknown or malformed",
        select=provider_lines,
        next_steps=[
            "Check the voice ID the app sends (config, env var, request) for typos.",
            "Confirm the voice exists on the account the key belongs to.",
            SUPPORT_STEP,
        ],
    )
