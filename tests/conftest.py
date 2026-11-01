import os
from pathlib import Path

REAL_GUARD = Path(__file__).resolve().parents[1] / "scripts" / "check-private-terms.sh"


def _override_warning() -> str | None:
    override = os.environ.get("TIDEWATCH_GUARD_SCRIPT")
    if override and Path(override).resolve() != REAL_GUARD:
        return (
            f"WARNING: TIDEWATCH_GUARD_SCRIPT is set; guard tests use {override}, "
            "NOT the real scripts/check-private-terms.sh. Unset it for a real run."
        )
    return None


def pytest_report_header(config):
    """Say loudly, at the top, when the guard tests run against a copy instead of the real guard."""
    warning = _override_warning()
    return [warning] if warning else []


def pytest_terminal_summary(terminalreporter):
    """Repeat it at the end, which is still shown with -q."""
    warning = _override_warning()
    if warning:
        terminalreporter.write_line(warning, red=True, bold=True)
