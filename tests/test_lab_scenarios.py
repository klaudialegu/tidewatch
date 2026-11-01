"""Each lab scenario is the healthy base plus exactly one intended change (rendered offline)."""

import copy
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "lab"
pytestmark = pytest.mark.skipif(shutil.which("kubectl") is None, reason="needs kubectl kustomize")


def render(path: Path) -> dict[tuple[str, str], dict]:
    out = subprocess.run(
        ["kubectl", "kustomize", str(path)], capture_output=True, text=True, check=True
    ).stdout
    return {(d["kind"], d["metadata"]["name"]): d for d in yaml.safe_load_all(out) if d}


def container(objects: dict) -> dict:
    return objects[("Deployment", "agent")]["spec"]["template"]["spec"]["containers"][0]


def expect_bad_image(base: dict) -> None:
    container(base)["image"] = "tidewatch-agent:0.0.0-missing"


def expect_no_key(base: dict) -> None:
    del container(base)["env"]


def expect_memory(base: dict) -> None:
    container(base)["env"].append({"name": "MEMORY_HOG", "value": "1"})
    container(base)["resources"]["limits"]["memory"] = "64Mi"


EXPECTED = {"bad-image": expect_bad_image, "no-key": expect_no_key, "memory": expect_memory}


def test_base_kustomization_renders_the_healthy_manifests():
    rendered = render(LAB / "k8s")
    files = {}
    for name in ("namespace.yaml", "deployment.yaml", "service.yaml"):
        for doc in yaml.safe_load_all((LAB / "k8s" / name).read_text()):
            files[(doc["kind"], doc["metadata"]["name"])] = doc
    assert rendered == files


@pytest.mark.parametrize("scenario", sorted(EXPECTED))
def test_scenario_changes_only_its_intended_fields(scenario):
    expected = copy.deepcopy(render(LAB / "k8s"))
    EXPECTED[scenario](expected)
    assert render(LAB / "scenarios" / scenario) == expected


def test_scenario_dirs_match_the_script_list():
    dirs = {p.name for p in (LAB / "scenarios").iterdir() if p.is_dir()}
    (listed,) = re.findall(r"^SCENARIOS=\(([^)]*)\)", (LAB / "scenario.sh").read_text(), re.M)
    assert dirs == set(listed.split()) == set(EXPECTED)
