"""The lab scripts may only ever act on tidewatch-lab."""

import os
import re
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = [
    ROOT / "lab" / name
    for name in ("cluster.sh", "patient.sh", "scenario.sh", "capture-fixtures.sh")
]
PATIENT = ROOT / "lab" / "patient.sh"
SCENARIO = ROOT / "lab" / "scenario.sh"
SCENARIO_NAMES = ("bad-image", "no-key", "memory")


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


# --- scenario.sh, against fake tools ---------------------------------------------------------


def make_scenario_env(tmp_path, cluster_exists=True, patient_deployed=True):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    clusters = "echo tidewatch-lab" if cluster_exists else ":"
    tools = {
        "kind": f'echo "kind $*" >> "{log}"\ncase "$*" in "get clusters") {clusters} ;; esac',
        "kubectl": f'echo "kubectl $*" >> "{log}"'
        + ("" if patient_deployed else '\ncase "$*" in *" get "*) exit 1 ;; esac'),
    }
    for name, body in tools.items():
        script = bin_dir / name
        script.write_text(f"#!/bin/sh\n{body}\nexit 0\n")
        script.chmod(0o755)
    state = tmp_path / "scenario-state"
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path), "SCENARIO_STATE": str(state)}
    return env, log, state


def run_scenario(env, *args):
    return subprocess.run(["bash", str(SCENARIO), *args], env=env, capture_output=True, text=True)


def calls(log):
    return log.read_text().splitlines() if log.exists() else []


def test_scenario_refuses_when_cluster_missing(tmp_path):
    env, log, state = make_scenario_env(tmp_path, cluster_exists=False)
    for args in (("apply", "no-key"), ("apply-random",), ("reset",)):
        result = run_scenario(env, *args)
        assert result.returncode == 1, args
        assert "cluster tidewatch-lab does not exist" in result.stderr
    assert not [c for c in calls(log) if c.startswith("kubectl")]
    assert not state.exists()


def test_scenario_rejects_unknown_name(tmp_path):
    env, log, _ = make_scenario_env(tmp_path)
    result = run_scenario(env, "apply", "bogus")
    assert result.returncode == 2
    assert "unknown scenario" in result.stderr
    assert calls(log) == []


def test_apply_random_hides_the_name_and_records_it(tmp_path):
    env, log, state = make_scenario_env(tmp_path)
    result = run_scenario(env, "apply-random")
    assert result.returncode == 0, result.stderr
    for name in SCENARIO_NAMES:
        assert name not in result.stdout + result.stderr
    picked = state.read_text().split()
    assert len(picked) == 1 and picked[0] in SCENARIO_NAMES
    applied = [c for c in calls(log) if " apply -k " in c]
    assert len(applied) == 1
    assert applied[0].startswith("kubectl --context kind-tidewatch-lab apply -k ")
    assert applied[0].endswith(f"/lab/scenarios/{picked[0]}")


def test_apply_random_does_not_repeat_until_round_restarts(tmp_path):
    env, _, state = make_scenario_env(tmp_path)
    for _ in SCENARIO_NAMES:
        assert run_scenario(env, "apply-random").returncode == 0
    assert sorted(state.read_text().split()) == sorted(SCENARIO_NAMES)
    exhausted = run_scenario(env, "apply-random")
    assert exhausted.returncode == 1
    assert "every scenario has been used" in exhausted.stderr
    assert run_scenario(env, "restart-round").returncode == 0
    assert run_scenario(env, "apply-random").returncode == 0


def test_reveal_names_the_current_scenario(tmp_path):
    env, _, state = make_scenario_env(tmp_path)
    assert "no scenario recorded" in run_scenario(env, "reveal").stdout
    run_scenario(env, "apply", "memory")
    assert run_scenario(env, "reveal").stdout.strip() == "current scenario: memory"
    run_scenario(env, "reset")
    assert "no scenario applied" in run_scenario(env, "reveal").stdout


def test_reset_applies_the_healthy_base_and_waits(tmp_path):
    env, log, _ = make_scenario_env(tmp_path)
    result = run_scenario(env, "reset")
    assert result.returncode == 0, result.stderr
    kubectl_calls = [c for c in calls(log) if c.startswith("kubectl") and " get " not in c]
    assert kubectl_calls[0].startswith("kubectl --context kind-tidewatch-lab apply -k ")
    assert kubectl_calls[0].endswith("/lab/k8s")
    assert "rollout status deployment/agent" in kubectl_calls[1]


def test_scenario_refuses_when_patient_not_deployed(tmp_path):
    env, log, state = make_scenario_env(tmp_path, patient_deployed=False)
    for args in (("apply", "no-key"), ("apply-random",), ("reset",)):
        result = run_scenario(env, *args)
        assert result.returncode == 1, args
        assert "the patient is not deployed" in result.stderr
    assert not [c for c in calls(log) if " apply " in c or "rollout" in c]
    assert not state.exists()


# --- capture-fixtures.sh, against fake tools and fake lab scripts ----------------------------

CAPTURE = ROOT / "lab" / "capture-fixtures.sh"


def make_capture_env(tmp_path, cluster_exists=True, leak_key=False):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    out = tmp_path / "fixtures"
    clusters = "echo tidewatch-lab" if cluster_exists else ":"
    body = "fake-key-123" if leak_key else "clean"
    scripts = {
        bin_dir
        / "kind": f'echo "kind $*" >> "{log}"\ncase "$*" in "get clusters") {clusters} ;; esac',
        bin_dir / "kubectl": f'echo "kubectl $*" >> "{log}"\nprintf %s {FAKE_KEY_B64}',
        # fake `uv run ... -o FILE`: write FILE (last argument)
        bin_dir / "uv": f'echo "uv $*" >> "{log}"\nfor a; do f="$a"; done\necho "{body}" > "$f"',
        tmp_path / "patient.sh": f'echo "patient $*" >> "{log}"',
        tmp_path / "scenario.sh": f'echo "scenario $*" >> "{log}"\n[ "$1" = list ] && echo no-key',
    }
    for path, text in scripts.items():
        path.write_text(f"#!/bin/sh\n{text}\nexit 0\n")
        path.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "CAPTURE_OUT": str(out),
        "CAPTURE_PATIENT": str(tmp_path / "patient.sh"),
        "CAPTURE_SCENARIO": str(tmp_path / "scenario.sh"),
        "CAPTURE_WAIT_SECONDS": "0",
    }
    return env, log, out


def run_capture(env, *args):
    return subprocess.run(["bash", str(CAPTURE), *args], env=env, capture_output=True, text=True)


def test_capture_refuses_when_cluster_missing(tmp_path):
    env, log, out = make_capture_env(tmp_path, cluster_exists=False)
    result = run_capture(env)
    assert result.returncode == 1
    assert "cluster tidewatch-lab does not exist" in result.stderr
    assert [c for c in calls(log) if not c.startswith("kind ")] == []
    assert not out.exists()


def test_capture_deletes_a_fixture_that_contains_the_lab_key(tmp_path):
    env, _, out = make_capture_env(tmp_path, leak_key=True)
    result = run_capture(env, "healthy")
    assert result.returncode == 1
    assert "contained the lab API key; deleted it" in result.stderr
    assert not (out / "healthy.json").exists()
    assert "fake-key-123" not in result.stdout + result.stderr


def test_capture_writes_clean_fixtures_and_ends_healthy(tmp_path):
    env, log, out = make_capture_env(tmp_path)
    result = run_capture(env)
    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in out.iterdir()) == ["healthy.json", "no-key.json"]
    lab_calls = [c for c in calls(log) if c.startswith(("patient", "scenario apply"))]
    assert lab_calls == [
        "patient down",
        "patient up",
        "patient down",
        "patient up",
        "scenario apply no-key",
        "patient down",
        "patient up",
    ]
