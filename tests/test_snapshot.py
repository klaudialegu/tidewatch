from pathlib import Path

from fakes import T0

from tidewatch.snapshot import (
    SCHEMA_VERSION,
    ContainerState,
    ContainerStatus,
    Event,
    Pod,
    Snapshot,
)


def test_snapshot_json_round_trip_keeps_everything():
    snap = Snapshot(
        collected_at=T0,
        context="kind-tidewatch-lab",
        namespace="demo",
        pods=[
            Pod(
                name="agent-abc",
                phase="Running",
                containers=[
                    ContainerStatus(
                        name="agent",
                        image="tidewatch-agent:0.1.0",
                        ready=False,
                        restart_count=2,
                        state=ContainerState(state="waiting", reason="CrashLoopBackOff"),
                        last_state=ContainerState(state="terminated", reason="Error", exit_code=1),
                        previous_logs=["fatal: something"],
                    )
                ],
            )
        ],
        events=[
            Event(type="Warning", reason="BackOff", object="Pod/agent-abc", message="m", count=3)
        ],
    )
    again = Snapshot.model_validate_json(snap.model_dump_json())
    assert again == snap


def test_snapshot_has_schema_version_and_no_secret_fields():
    snap = Snapshot(collected_at=T0, context=None, namespace="demo")
    assert snap.model_dump()["schema_version"] == SCHEMA_VERSION == "1"
    import inspect

    from pydantic import BaseModel

    import tidewatch.snapshot as module

    models = [
        m
        for _, m in inspect.getmembers(module, inspect.isclass)
        if issubclass(m, BaseModel) and m.__module__ == module.__name__
    ]
    assert len(models) >= 7  # every model in the module, including ContainerSpec and Deployment
    field_names = set()
    for model in models:
        field_names |= set(model.model_fields)
    assert not field_names & {"env", "secrets", "data", "value", "string_data"}


def test_load_rejects_unsupported_schema_version(tmp_path):
    import json

    import pytest

    from tidewatch.snapshot import SnapshotLoadError, load

    path = tmp_path / "v2.json"
    path.write_text(json.dumps({"schema_version": "2", "resources": {"different": "shape"}}))
    with pytest.raises(SnapshotLoadError, match="unsupported schema_version '2'"):
        load(path)


def test_load_rejects_namespace_mismatch():
    import pytest

    from tidewatch.snapshot import SnapshotLoadError, load

    path = Path(__file__).resolve().parent / "fixtures" / "healthy.json"
    with pytest.raises(SnapshotLoadError, match="is for namespace 'demo', not 'prod'"):
        load(path, namespace="prod")


def test_load_reads_a_fixture():
    from tidewatch.snapshot import load

    snap = load(Path(__file__).resolve().parent / "fixtures" / "memory.json", namespace="demo")
    assert (snap.namespace, snap.schema_version) == ("demo", "1")


def test_load_rejects_invalid_v1_snapshot(tmp_path):
    import pytest

    from tidewatch.snapshot import SnapshotLoadError, load

    path = tmp_path / "bad.json"
    path.write_text('{"schema_version": "1", "namespace": "demo", "pods": "not a list"}')
    with pytest.raises(SnapshotLoadError, match="invalid snapshot"):
        load(path)
