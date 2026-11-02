"""Static checks on the lab patient: manifests, Dockerfile, and Tidewatch's own dependencies."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "lab"
DOCKERFILE = LAB / "agent" / "Dockerfile"


def load_docs(path: Path) -> list[dict]:
    return [doc for doc in yaml.safe_load_all(path.read_text()) if doc]


def deployment() -> dict:
    (doc,) = load_docs(LAB / "k8s" / "deployment.yaml")
    assert doc["kind"] == "Deployment"
    return doc


def agent_container() -> dict:
    (container,) = deployment()["spec"]["template"]["spec"]["containers"]
    return container


def dockerfile_uid() -> int:
    users = re.findall(r"^USER\s+(\S+)", DOCKERFILE.read_text(), re.M)
    assert len(users) == 1, users
    return int(users[0].split(":")[0])


def test_tidewatch_project_has_no_fastapi():
    for name in ("pyproject.toml", "uv.lock"):
        text = (ROOT / name).read_text().lower()
        assert "fastapi" not in text, name
        assert "uvicorn" not in text, name


def test_dockerfile_base_pinned_by_digest_and_numeric_user():
    text = DOCKERFILE.read_text()
    froms = re.findall(r"^FROM\s+(\S+)", text, re.M)
    assert froms, "no FROM line"
    for image in froms:
        assert re.search(r"@sha256:[0-9a-f]{64}$", image), image
    assert dockerfile_uid() > 0  # numeric (int() would fail on a name) and not root
    syncs = re.findall(r"^RUN\s+uv sync.*$", text, re.M)
    assert syncs, "no uv sync line"
    for line in syncs:
        assert "--no-dev" in line and "--frozen" in line, line


def test_deployment_runs_as_numeric_non_root_user():
    pod_security = deployment()["spec"]["template"]["spec"]["securityContext"]
    assert pod_security["runAsNonRoot"] is True
    assert pod_security["runAsUser"] == dockerfile_uid()


def test_deployment_reads_api_key_from_secret_ref():
    env = {e["name"]: e for e in agent_container()["env"]}
    api_key = env["API_KEY"]
    assert "value" not in api_key
    assert api_key["valueFrom"]["secretKeyRef"] == {"name": "agent-secret", "key": "API_KEY"}


def test_deployment_has_memory_limit_and_probes_on_health():
    container = agent_container()
    assert container["resources"]["limits"]["memory"]
    assert container["resources"]["requests"]["memory"]
    for probe in ("readinessProbe", "livenessProbe"):
        assert container[probe]["httpGet"]["path"] == "/health", probe


def test_deployment_image_tag_is_pinned():
    image = agent_container()["image"]
    name, sep, tag = image.rpartition(":")
    assert sep and name and tag and tag != "latest", image
    assert f'IMAGE="{image}"' in (LAB / "patient.sh").read_text()


def test_all_manifests_target_namespace_demo():
    paths = [p for p in sorted((LAB / "k8s").glob("*.yaml")) if p.name != "kustomization.yaml"]
    docs = [doc for path in paths for doc in load_docs(path)]
    assert {doc["kind"] for doc in docs} >= {"Namespace", "Deployment", "Service"}
    for doc in docs:
        if doc["kind"] == "Namespace":
            assert doc["metadata"]["name"] == "demo"
        else:
            assert doc["metadata"]["namespace"] == "demo", doc["kind"]


def test_no_secret_manifest_committed():
    for path in sorted(LAB.rglob("*.y*ml")):
        if ".venv" in path.parts:
            continue
        for doc in load_docs(path):
            assert doc.get("kind") != "Secret", path
