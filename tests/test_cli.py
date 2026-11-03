from importlib.metadata import version
from pathlib import Path

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


# --- scan -------------------------------------------------------------------------------------

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _no_cluster(monkeypatch):
    import tidewatch.cli as cli

    def explode(context):
        raise AssertionError("scan --from must not load cluster clients")

    monkeypatch.setattr(cli, "load_clients", explode)


def _snapshot_file(tmp_path, logs, namespace="demo", version="1"):
    import json

    data = json.loads((FIXTURES / "no-key.json").read_text())
    data["pods"][0]["containers"][0]["previous_logs"] = logs
    data["namespace"], data["schema_version"] = namespace, version
    path = tmp_path / "snap.json"
    path.write_text(json.dumps(data))
    return path


def test_scan_from_file_prints_findings(monkeypatch):
    _no_cluster(monkeypatch)
    result = CliRunner().invoke(app, ["scan", "--from", str(FIXTURES / "memory.json")])
    assert result.exit_code == 0, result.output
    assert (
        "CRITICAL  Container killed for exceeding its memory limit  [oom-killed]" in result.stdout
    )
    assert "memory limit: 64Mi" in result.stdout
    assert "Next steps:" in result.stdout


def test_scan_from_file_healthy_prints_no_findings(monkeypatch):
    _no_cluster(monkeypatch)
    result = CliRunner().invoke(app, ["scan", "--from", str(FIXTURES / "healthy.json")])
    assert result.exit_code == 0
    assert "No findings in kind-tidewatch-lab/demo." in result.stdout


def test_scan_from_file_never_loads_clients(monkeypatch):
    _no_cluster(monkeypatch)
    for name in ("healthy", "bad-image", "no-key", "memory"):
        result = CliRunner().invoke(app, ["scan", "--from", str(FIXTURES / f"{name}.json")])
        assert result.exit_code == 0, (name, result.output)


def test_scan_json_output_is_a_valid_report(monkeypatch):
    from tidewatch.findings import Report

    _no_cluster(monkeypatch)
    result = CliRunner().invoke(app, ["scan", "--from", str(FIXTURES / "bad-image.json"), "--json"])
    assert result.exit_code == 0
    report = Report.model_validate_json(result.stdout)
    assert report.schema_version == "1"
    assert [f.rule_id for f in report.findings] == ["image-pull-failed"]
    assert "CRITICAL" not in result.stdout  # JSON only, no text report


def test_scan_writes_report_file(monkeypatch, tmp_path):
    from tidewatch.findings import Report

    _no_cluster(monkeypatch)
    out = tmp_path / "report.json"
    result = CliRunner().invoke(
        app, ["scan", "--from", str(FIXTURES / "no-key.json"), "-o", str(out)]
    )
    assert result.exit_code == 0
    assert "CRITICAL" in result.stdout  # text report still printed
    assert [f.rule_id for f in Report.model_validate_json(out.read_text()).findings] == [
        "container-exits-on-start"
    ]


def test_scan_json_and_output_file_together(monkeypatch, tmp_path):
    _no_cluster(monkeypatch)
    out = tmp_path / "report.json"
    args = ["scan", "--from", str(FIXTURES / "memory.json"), "--json", "-o", str(out)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0
    assert result.stdout == out.read_text()


def test_scan_reports_unreadable_snapshot_file(monkeypatch, tmp_path):
    _no_cluster(monkeypatch)
    for path in (tmp_path / "missing.json", tmp_path / "broken.json"):
        if path.name == "broken.json":
            path.write_text("{not json")
        result = CliRunner().invoke(app, ["scan", "--from", str(path)])
        assert result.exit_code == 1, path.name
        assert "cannot read snapshot" in result.stderr
        assert "Traceback" not in result.output


def test_scan_rejects_unsupported_snapshot_schema_version(monkeypatch, tmp_path):
    _no_cluster(monkeypatch)
    path = _snapshot_file(tmp_path, [], version="2")
    result = CliRunner().invoke(app, ["scan", "--from", str(path)])
    assert result.exit_code == 1
    assert "unsupported schema_version '2'" in result.stderr


def test_scan_from_file_rejects_namespace_mismatch(monkeypatch):
    _no_cluster(monkeypatch)
    result = CliRunner().invoke(
        app, ["scan", "--from", str(FIXTURES / "healthy.json"), "-n", "prod"]
    )
    assert result.exit_code == 1
    assert "is for namespace 'demo', not 'prod'" in result.stderr


def test_scan_reports_missing_namespace(monkeypatch):
    _fake_clients(monkeypatch)
    result = CliRunner().invoke(app, ["scan", "-n", "demoo"])
    assert result.exit_code == 1
    assert "namespace 'demoo' not found in kind-test" in result.stderr


def test_scan_reports_cluster_error_cleanly(monkeypatch):
    _fake_clients(monkeypatch, error=RuntimeError("connection refused"))
    result = CliRunner().invoke(app, ["scan", "-n", "demo"])
    assert result.exit_code == 1
    assert "could not collect from the cluster: connection refused" in result.stderr
    assert "Traceback" not in result.output


def test_scan_prints_log_lines_with_brackets_literally(monkeypatch, tmp_path):
    _no_cluster(monkeypatch)
    logs = ["error: path [/tmp] done", "fatal: [bold red]x[/] boom"]
    result = CliRunner().invoke(app, ["scan", "--from", str(_snapshot_file(tmp_path, logs))])
    assert result.exit_code == 0, result.output
    assert "| error: path [/tmp] done" in result.stdout
    assert "| fatal: [bold red]x[/] boom" in result.stdout


def test_cli_errors_with_brackets_print_literally(monkeypatch):
    _fake_clients(monkeypatch, error=RuntimeError("bad [/x] path"))
    for command in ("collect", "scan"):
        result = CliRunner().invoke(app, [command, "-n", "demo"])
        assert result.exit_code == 1, command
        assert "could not collect from the cluster: bad [/x] path" in result.stderr
        assert "Traceback" not in result.output
