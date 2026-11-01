"""The lab scripts may only ever act on tidewatch-lab."""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = [ROOT / "lab" / "cluster.sh", ROOT / "lab" / "patient.sh"]
PATIENT = ROOT / "lab" / "patient.sh"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_every_kubectl_call_passes_lab_context(script):
    text = script.read_text()
    assert 'CLUSTER="tidewatch-lab"' in text
    assert 'CONTEXT="kind-${CLUSTER}"' in text
    calls = 0
    for number, line in enumerate(text.splitlines(), 1):
        code = line.split("#", 1)[0] if line.lstrip().startswith("#") else line
        if not re.search(r"\bkubectl\b", code):
            continue
        if re.search(r"\bneed kubectl\b", code) or "kubectl config current-context" in code:
            continue  # tool check, and a read of the local kubeconfig (no cluster call)
        calls += 1
        assert '--context "${CONTEXT}"' in code, f"{script.name}:{number}: {line.strip()}"
    assert calls > 0


@pytest.fixture
def fake_tools(tmp_path):
    """Fake docker, kind, kubectl and openssl that log their arguments; kind lists no clusters."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    for tool in ("docker", "kind", "kubectl", "openssl"):
        script = bin_dir / tool
        script.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{log}"\nexit 0\n')
        script.chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)}
    return env, log


def run_patient(env, *args):
    return subprocess.run(["bash", str(PATIENT), *args], env=env, capture_output=True, text=True)


def test_patient_refuses_when_cluster_missing(fake_tools):
    env, log = fake_tools
    result = run_patient(env, "up")
    assert result.returncode == 1
    assert "cluster tidewatch-lab does not exist" in result.stderr
    calls = log.read_text().splitlines() if log.exists() else []
    assert "kind get clusters" in calls  # it did look
    assert not [c for c in calls if c.startswith("kubectl")], calls
    assert not [c for c in calls if c.startswith("docker build")], calls
    assert not [c for c in calls if c.startswith("kind load")], calls


def test_patient_rejects_unknown_command(fake_tools):
    env, log = fake_tools
    result = run_patient(env, "bogus")
    assert result.returncode == 2
    assert "usage:" in result.stderr
    assert not log.exists()
