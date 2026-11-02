"""Fake Kubernetes API objects and clients for tests (shaped like the official client's models)."""

from datetime import UTC, datetime
from types import SimpleNamespace as NS

ALLOWED = {
    "read_namespace",
    "list_namespaced_pod",
    "list_namespaced_event",
    "read_namespaced_pod_log",
}
ALLOWED_APPS = {"list_namespaced_deployment"}
T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def state(waiting=None, running=False, terminated=None):
    return NS(
        waiting=NS(reason=waiting[0], message=waiting[1]) if waiting else None,
        running=NS() if running else None,
        terminated=(
            NS(reason=terminated[0], message=None, exit_code=terminated[1]) if terminated else None
        ),
    )


def container_status(
    name="agent", image="tidewatch-agent:0.1.0", ready=True, restarts=0, st=None, last=None
):
    return NS(
        name=name,
        image=image,
        ready=ready,
        restart_count=restarts,
        state=st or state(running=True),
        last_state=last,
    )


def pod(name="agent-abc", phase="Running", statuses=None, ready="True"):
    return NS(
        metadata=NS(
            name=name, owner_references=[NS(kind="ReplicaSet", name="agent-rs", controller=True)]
        ),
        status=NS(
            phase=phase,
            conditions=[NS(type="Ready", status=ready), NS(type="PodScheduled", status="True")],
            container_statuses=statuses if statuses is not None else [container_status()],
        ),
    )


def deployment(name="agent", image="tidewatch-agent:0.1.0", ready=1):
    container = NS(
        name="agent",
        image=image,
        resources=NS(
            limits={"memory": "128Mi", "cpu": "250m"}, requests={"memory": "64Mi", "cpu": "50m"}
        ),
    )
    return NS(
        metadata=NS(name=name),
        spec=NS(replicas=1, template=NS(spec=NS(containers=[container]))),
        status=NS(ready_replicas=ready, available_replicas=ready),
    )


def event(reason="Scheduled", kind="Pod", name="agent-abc", typ="Normal", count=1, when=T0):
    return NS(
        type=typ,
        reason=reason,
        involved_object=NS(kind=kind, name=name),
        message=f"{reason} message",
        count=count,
        first_timestamp=when,
        last_timestamp=when,
        event_time=None,
    )


class FakeCore:
    """Only the allowed read calls exist; anything else fails the test loudly."""

    def __init__(self, pods=(), events=(), logs=None, log_error=None, namespaces=("demo",)):
        self._pods, self._events = list(pods), list(events)
        self._logs, self._log_error = logs, log_error
        self._namespaces = set(namespaces)
        self.calls = []

    def read_namespace(self, name):
        from kubernetes.client.exceptions import ApiException

        self.calls.append(("read_namespace", name))
        if name not in self._namespaces:
            raise ApiException(status=404, reason="Not Found")
        return NS(metadata=NS(name=name))

    def list_namespaced_pod(self, namespace):
        self.calls.append(("list_namespaced_pod", namespace))
        return NS(items=self._pods)

    def list_namespaced_event(self, namespace):
        self.calls.append(("list_namespaced_event", namespace))
        return NS(items=self._events)

    def read_namespaced_pod_log(self, name, namespace, **kwargs):
        self.calls.append(("read_namespaced_pod_log", name, namespace, kwargs))
        if self._log_error:
            raise self._log_error
        return self._logs

    def __getattr__(self, attr):
        raise AssertionError(f"collect called a non-allowed CoreV1Api method: {attr}")


class FakeApps:
    def __init__(self, deployments=()):
        self._deployments = list(deployments)
        self.calls = []

    def list_namespaced_deployment(self, namespace):
        self.calls.append(("list_namespaced_deployment", namespace))
        return NS(items=self._deployments)

    def __getattr__(self, attr):
        raise AssertionError(f"collect called a non-allowed AppsV1Api method: {attr}")
