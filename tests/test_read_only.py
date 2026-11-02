"""Tidewatch is read-only against clusters (CLAUDE.md). This scans its source for any write call."""

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "tidewatch"
WRITE = re.compile(r"^(create|patch|replace|delete|connect|post|put|update)_")
API_CALL = re.compile(r"_(namespaced|cluster|for_all_namespaces)|_namespaced_")


def attribute_calls(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text())
    return [
        (node.lineno, node.func.attr)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]


def sources() -> list[Path]:
    files = sorted(SRC.rglob("*.py"))
    assert files, "no source files found"
    return files


def test_source_never_calls_write_methods():
    offenders = [
        f"{path.name}:{line} {name}"
        for path in sources()
        for line, name in attribute_calls(path)
        if WRITE.match(name)
    ]
    assert offenders == []


def test_source_kubernetes_api_calls_are_list_or_read():
    api_calls = [
        (path.name, line, name)
        for path in sources()
        for line, name in attribute_calls(path)
        if API_CALL.search(name)
    ]
    assert api_calls, "expected at least one Kubernetes API call"
    assert [c for c in api_calls if not c[2].startswith(("list_", "read_"))] == []


def test_source_never_imports_exec_streaming():
    for path in sources():
        text = path.read_text()
        assert "kubernetes.stream" not in text, path.name
        assert "from kubernetes import stream" not in text, path.name
