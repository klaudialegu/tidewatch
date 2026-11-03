"""Small builders for Snapshot objects in rule and diagnose tests."""

from fakes import T0

from tidewatch.snapshot import (
    ContainerSpec,
    ContainerState,
    ContainerStatus,
    Deployment,
    Event,
    Pod,
    Snapshot,
)

RUNNING = ContainerState(state="running")


def cstatus(
    name="agent",
    image="tidewatch-agent:0.1.0",
    ready=True,
    restarts=0,
    state=None,
    last=None,
    logs=(),
):
    return ContainerStatus(
        name=name,
        image=image,
        ready=ready,
        restart_count=restarts,
        state=state or RUNNING,
        last_state=last,
        previous_logs=list(logs),
    )


def waiting(reason, message=None):
    return ContainerState(state="waiting", reason=reason, message=message)


def terminated(reason, code):
    return ContainerState(state="terminated", reason=reason, exit_code=code)


def mkpod(name="agent-7c8b696666-abcde", containers=None, owner="ReplicaSet/agent-7c8b696666"):
    return Pod(name=name, phase="Running", owner=owner, containers=containers or [cstatus()])


def mkdeployment(name="agent", memory_limit="64Mi"):
    return Deployment(
        name=name,
        replicas=1,
        ready_replicas=0,
        available_replicas=0,
        containers=[
            ContainerSpec(name="agent", image="tidewatch-agent:0.1.0", memory_limit=memory_limit)
        ],
    )


def mkevent(reason, obj, message, typ="Warning", count=1):
    return Event(
        type=typ,
        reason=reason,
        object=obj,
        message=message,
        count=count,
        first_seen=T0,
        last_seen=T0,
    )


def mksnapshot(pods=(), deployments=(), events=()):
    return Snapshot(
        collected_at=T0,
        context="kind-test",
        namespace="demo",
        pods=list(pods),
        deployments=list(deployments),
        events=list(events),
    )
