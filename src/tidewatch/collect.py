"""Collect a Snapshot from a cluster. Read-only: list_* and read_* calls only (test_read_only)."""

from datetime import UTC, datetime
from typing import Any

from tidewatch.snapshot import (
    ContainerSpec,
    ContainerState,
    ContainerStatus,
    Deployment,
    Event,
    Pod,
    Snapshot,
)

LOG_LINES = 50
LOG_SINCE_SECONDS = 3600


class NamespaceNotFound(Exception):
    """The namespace doesn't exist (a typo must not look like an empty, healthy namespace)."""


def load_clients(context: str | None = None) -> tuple[Any, Any, str | None]:
    """Return (CoreV1Api, AppsV1Api, context name) from the local kubeconfig."""
    from kubernetes import client, config

    config.load_kube_config(context=context)
    if context is None:
        _, active = config.list_kube_config_contexts()
        context = active["name"] if active else None
    return client.CoreV1Api(), client.AppsV1Api(), context


def _state(state: Any) -> ContainerState | None:
    if state is None:
        return None
    if state.waiting:
        return ContainerState(
            state="waiting", reason=state.waiting.reason, message=state.waiting.message
        )
    if state.terminated:
        t = state.terminated
        return ContainerState(
            state="terminated", reason=t.reason, message=t.message, exit_code=t.exit_code
        )
    if state.running:
        return ContainerState(state="running")
    return None


def _logs(
    core: Any,
    namespace: str,
    pod: str,
    container: str,
    *,
    previous: bool,
    lines: int,
    since_seconds: int,
) -> list[str]:
    """A bounded log tail: never more than `lines` lines or older than `since_seconds`."""
    try:
        text = core.read_namespaced_pod_log(
            pod,
            namespace,
            container=container,
            previous=previous,
            tail_lines=lines,
            since_seconds=since_seconds,
            timestamps=not previous,
        )
    except Exception:  # no such run, pod gone, or no permission: logs are best-effort
        return []
    return text.splitlines()[-lines:] if text else []


def _pod(core: Any, namespace: str, item: Any, lines: int, since_seconds: int) -> Pod:
    owners = item.metadata.owner_references or []
    owner = next((f"{o.kind}/{o.name}" for o in owners if o.controller), None)
    containers = []
    for status in item.status.container_statuses or []:
        restarts = status.restart_count or 0
        limits = {"lines": lines, "since_seconds": since_seconds}
        containers.append(
            ContainerStatus(
                name=status.name,
                image=status.image,
                ready=bool(status.ready),
                restart_count=restarts,
                state=_state(status.state) or ContainerState(state="unknown"),
                last_state=_state(status.last_state),
                previous_logs=(
                    _logs(core, namespace, item.metadata.name, status.name, previous=True, **limits)
                    if restarts
                    else []
                ),
                recent_logs=_logs(
                    core, namespace, item.metadata.name, status.name, previous=False, **limits
                ),
            )
        )
    return Pod(
        name=item.metadata.name,
        phase=item.status.phase,
        owner=owner,
        labels=dict(item.metadata.labels or {}),
        conditions={c.type: c.status for c in item.status.conditions or []},
        containers=containers,
    )


def _deployment(item: Any) -> Deployment:
    specs = []
    for c in item.spec.template.spec.containers:
        resources = c.resources
        limits = (resources.limits if resources else None) or {}
        requests = (resources.requests if resources else None) or {}
        specs.append(
            ContainerSpec(
                name=c.name,
                image=c.image,
                memory_request=requests.get("memory"),
                memory_limit=limits.get("memory"),
                cpu_request=requests.get("cpu"),
                cpu_limit=limits.get("cpu"),
            )
        )
    status = item.status
    return Deployment(
        name=item.metadata.name,
        replicas=item.spec.replicas or 0,
        ready_replicas=status.ready_replicas or 0,
        available_replicas=status.available_replicas or 0,
        containers=specs,
    )


def _event(item: Any) -> Event:
    obj = item.involved_object
    return Event(
        type=item.type,
        reason=item.reason,
        object=f"{obj.kind}/{obj.name}",
        message=item.message,
        count=item.count or 1,
        first_seen=item.first_timestamp or item.event_time,
        last_seen=item.last_timestamp or item.event_time,
    )


def collect(
    core: Any,
    apps: Any,
    namespace: str,
    context: str | None = None,
    now: datetime | None = None,
    selector: str | None = None,
    log_lines: int = LOG_LINES,
    log_since_seconds: int = LOG_SINCE_SECONDS,
) -> Snapshot:
    from kubernetes.client.exceptions import ApiException

    try:
        core.read_namespace(namespace)
    except ApiException as exc:
        if exc.status == 404:
            raise NamespaceNotFound(namespace) from None
        raise
    pods = core.list_namespaced_pod(namespace, label_selector=selector).items
    deployments = apps.list_namespaced_deployment(namespace).items
    events = core.list_namespaced_event(namespace).items
    return Snapshot(
        collected_at=now or datetime.now(UTC),
        context=context,
        namespace=namespace,
        selector=selector,
        log_lines=log_lines,
        log_since_seconds=log_since_seconds,
        pods=sorted(
            (_pod(core, namespace, p, log_lines, log_since_seconds) for p in pods),
            key=lambda p: p.name,
        ),
        deployments=sorted((_deployment(d) for d in deployments), key=lambda d: d.name),
        events=sorted(
            (_event(e) for e in events),
            key=lambda e: (e.last_seen or datetime.min.replace(tzinfo=UTC), e.object),
        ),
    )
