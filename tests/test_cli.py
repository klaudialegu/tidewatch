from importlib.metadata import version

from typer.testing import CliRunner

from tidewatch.cli import app


def test_version_prints_package_version():
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == version("tidewatch")


def _fake_clients(monkeypatch, error=None):
    from fakes import FakeApps, FakeCore, deployment, event, pod

    import tidewatch.cli as cli

    def fake_load(context):
        if error:
            raise error
        return FakeCore(pods=[pod()], events=[event()]), FakeApps([deployment()]), "kind-test"

    monkeypatch.setattr(cli, "load_clients", fake_load)


def test_collect_writes_snapshot_json_file(monkeypatch, tmp_path):
    from tidewatch.snapshot import Snapshot

    _fake_clients(monkeypatch)
    out = tmp_path / "snapshot.json"
    result = CliRunner().invoke(app, ["collect", "-n", "demo", "-o", str(out)])
    assert result.exit_code == 0, result.output
    snap = Snapshot.model_validate_json(out.read_text())
    assert (snap.namespace, snap.context, len(snap.pods)) == ("demo", "kind-test", 1)


def test_collect_prints_json_to_stdout_without_output(monkeypatch):
    import json

    _fake_clients(monkeypatch)
    result = CliRunner().invoke(app, ["collect", "-n", "demo"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["namespace"] == "demo"


def test_collect_reports_cluster_error_cleanly(monkeypatch):
    _fake_clients(monkeypatch, error=RuntimeError("no such context"))
    result = CliRunner().invoke(app, ["collect", "-n", "demo"])
    assert result.exit_code == 1
    assert "could not collect from the cluster: no such context" in result.stderr
    assert "Traceback" not in result.output


def test_collect_reports_missing_namespace(monkeypatch):
    _fake_clients(monkeypatch)
    result = CliRunner().invoke(app, ["collect", "-n", "demoo"])
    assert result.exit_code == 1
    assert "namespace 'demoo' not found in kind-test" in result.stderr
    assert result.stdout == ""
