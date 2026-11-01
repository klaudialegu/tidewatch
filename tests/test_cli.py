from importlib.metadata import version

from typer.testing import CliRunner

from tidewatch.cli import app


def test_version_prints_package_version():
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == version("tidewatch")
