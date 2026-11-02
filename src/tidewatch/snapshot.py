"""The snapshot: a versioned, JSON-serialisable picture of one namespace at one moment.

It holds only what diagnosis needs. Secrets and environment variable values are never collected.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

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


class Pod(BaseModel):
    name: str
    phase: str | None
    owner: str | None = Field(None, description='"Kind/name" of the controlling owner.')
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
    pods: list[Pod] = Field(default_factory=list)
    deployments: list[Deployment] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
