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
