"""The snapshot: a versioned, JSON-serialisable picture of one namespace at one moment.

It holds only what diagnosis needs. Secrets and environment variable values are never collected.
Logs are: a bounded tail (line count and time window) of the previous and current run of each
container. Logs can contain anything an app prints, which is why snapshots and reports must not be
shared outside the team until redaction exists (ADR 0003, ADR 0004).

Schema policy (ADR 0004): adding an optional field keeps schema_version "1"; renaming, removing or
changing the meaning of a field needs "2".
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

SCHEMA_VERSION = "1"


class ContainerState(BaseModel):
    state: Literal["waiting", "running", "terminated", "unknown"]
    reason: str | None = None
    message: str | None = None
    exit_code: int | None = None


class ContainerStatus(BaseModel):
    name: str
    image: str
    ready: bool
    restart_count: int
    state: ContainerState
    last_state: ContainerState | None = None
    previous_logs: list[str] = Field(
        default_factory=list, description="Last lines from the previous run, if it restarted."
    )
    recent_logs: list[str] = Field(
        default_factory=list,
        description=(
            "Last lines of the current run, each '<RFC3339 timestamp> <message>' exactly as the "
            "Kubernetes API returns it with timestamps=true. Bounded by the snapshot's log limits."
        ),
    )


class Pod(BaseModel):
    name: str
    phase: str | None
    owner: str | None = Field(None, description='"Kind/name" of the controlling owner.')
    labels: dict[str, str] = Field(default_factory=dict)
    conditions: dict[str, str] = Field(default_factory=dict)
    containers: list[ContainerStatus] = Field(default_factory=list)


class ContainerSpec(BaseModel):
    name: str
    image: str
    memory_request: str | None = None
    memory_limit: str | None = None
    cpu_request: str | None = None
    cpu_limit: str | None = None


class Deployment(BaseModel):
    name: str
    replicas: int
    ready_replicas: int
    available_replicas: int
    containers: list[ContainerSpec] = Field(default_factory=list)


class Event(BaseModel):
    type: str | None
    reason: str | None
    object: str = Field(description='"Kind/name" of the object the event is about.')
    message: str | None
    count: int
    first_seen: datetime | None = None
    last_seen: datetime | None = None


class Snapshot(BaseModel):
    schema_version: str = SCHEMA_VERSION
    collected_at: datetime
    context: str | None
    namespace: str
    selector: str | None = Field(None, description="Label selector the pods were listed with.")
    log_lines: int | None = Field(None, description="Max log lines read per container and run.")
    log_since_seconds: int | None = Field(None, description="Log time window, in seconds.")
    pods: list[Pod] = Field(default_factory=list)
    deployments: list[Deployment] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)


class SnapshotLoadError(Exception):
    """A snapshot file can't be used: unreadable, wrong version, invalid, or wrong namespace."""


def load(path: str | Path, namespace: str | None = None) -> Snapshot:
    """Load a saved snapshot. The schema_version is checked before full validation."""
    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise SnapshotLoadError(f"cannot read snapshot {path}: {exc}") from None
    version = raw.get("schema_version") if isinstance(raw, dict) else None
    if version != SCHEMA_VERSION:
        raise SnapshotLoadError(
            f"unsupported schema_version {version!r} in {path} "
            f"(this Tidewatch reads {SCHEMA_VERSION!r})"
        )
    try:
        snapshot = Snapshot.model_validate(raw)
    except ValidationError as exc:
        raise SnapshotLoadError(f"invalid snapshot {path}: {exc.error_count()} errors") from None
    if namespace is not None and snapshot.namespace != namespace:
        raise SnapshotLoadError(
            f"snapshot {path} is for namespace {snapshot.namespace!r}, not {namespace!r}"
        )
    return snapshot
