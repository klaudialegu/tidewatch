"""The lab scripts may only ever act on tidewatch-lab."""

import os
import re
import subprocess
import time
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


# --- patient.sh check, against fake tools (the cluster "exists", nothing real is called) -------

FAKE_KEY_B64 = "ZmFrZS1rZXktMTIz"  # base64 of "fake-key-123"  # gitleaks:allow (fake test value)


def make_check_env(
    tmp_path,
    health='{"status":"ok"}',
    chat='{"reply":"echo: ping"}',
    curl_exit=0,
    logs="INFO: GET /health 200",
    logs_exit=0,
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    pid_file = tmp_path / "port-forward.pid"
    tools = {
        "docker": "exit 0",
        "kind": 'case "$*" in "get clusters") echo tidewatch-lab ;; esac',
        "kubectl": f"""case "$*" in
  *port-forward*) echo $$ > "{pid_file}"; exec sleep 30 ;;
  *"get secret"*) printf '%s' '{FAKE_KEY_B64}' ;;
  *logs*) printf '%s' '{logs}'; exit {logs_exit} ;;
esac""",
        "curl": f"""case "$*" in
  */health*) printf '%s' '{health}' ;;
  */chat*) printf '%s' '{chat}' ;;
esac
exit {curl_exit}""",
    }
    for name, body in tools.items():
        script = bin_dir / name
        script.write_text(f"#!/bin/sh\n{body}\n")
        script.chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path), "CHECK_WAIT_TRIES": "2"}
    return env, pid_file


def process_alive(pid: int) -> bool:
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        time.sleep(0.1)
    return True


def test_patient_check_passes_and_stops_its_port_forward(tmp_path):
    env, pid_file = make_check_env(tmp_path)
    result = run_patient(env, "check")
    assert result.returncode == 0, result.stderr
    assert "API key not present" in result.stdout
    assert not process_alive(int(pid_file.read_text()))


def test_patient_check_fails_when_health_unreachable(tmp_path):
    env, pid_file = make_check_env(tmp_path, health="", curl_exit=7)
    result = run_patient(env, "check")
    assert result.returncode == 1
    assert "FAIL: /health did not answer" in result.stderr
    assert not process_alive(int(pid_file.read_text()))


def test_patient_check_fails_on_wrong_health_reply(tmp_path):
    env, _ = make_check_env(tmp_path, health='{"status":"bad"}')
    result = run_patient(env, "check")
    assert result.returncode == 1
    assert "FAIL: unexpected /health reply" in result.stderr


def test_patient_check_fails_on_wrong_chat_reply(tmp_path):
    env, _ = make_check_env(tmp_path, chat='{"reply":"nope"}')
    result = run_patient(env, "check")
    assert result.returncode == 1
    assert "FAIL: unexpected /chat reply" in result.stderr


def test_patient_check_fails_when_key_in_logs(tmp_path):
    env, _ = make_check_env(tmp_path, logs="starting with key fake-key-123")
    result = run_patient(env, "check")
    assert result.returncode == 1
    assert "API key appears in the pod logs" in result.stderr
    assert "fake-key-123" not in result.stdout + result.stderr


def test_patient_check_fails_when_logs_unreadable(tmp_path):
    env, _ = make_check_env(tmp_path, logs="error: pod not found", logs_exit=1)
    result = run_patient(env, "check")
    assert result.returncode == 1
    assert "FAIL: could not read the pod logs" in result.stderr


def test_patient_check_fails_when_logs_empty(tmp_path):
    env, _ = make_check_env(tmp_path, logs="")
    result = run_patient(env, "check")
    assert result.returncode == 1
    assert "FAIL: the pod logs are empty" in result.stderr
