from conftest import REAL_GUARD, _override_warning


def test_override_warning_when_guard_is_a_copy(monkeypatch, tmp_path):
    monkeypatch.setenv("TIDEWATCH_GUARD_SCRIPT", str(tmp_path / "broken-guard.sh"))
    warning = _override_warning()
    assert warning is not None
    assert "NOT the real scripts/check-private-terms.sh" in warning


def test_no_override_warning_for_real_guard_or_unset(monkeypatch):
    monkeypatch.setenv("TIDEWATCH_GUARD_SCRIPT", str(REAL_GUARD))
    assert _override_warning() is None
    monkeypatch.delenv("TIDEWATCH_GUARD_SCRIPT")
    assert _override_warning() is None
