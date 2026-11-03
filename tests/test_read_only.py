"""Tidewatch is read-only against clusters (CLAUDE.md). This scans its source for any way to write.

It is an allowlist: every Kubernetes API call must be one of ALLOWED_API_CALLS. On top of that it
forbids the generic escape hatches (raw call_api/request, getattr, the dynamic client, exec streams)
and any method name that starts with a write verb.
"""

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "tidewatch"
ALLOWED_API_CALLS = {
    "read_namespace",
    "list_namespaced_pod",
    "list_namespaced_event",
    "read_namespaced_pod_log",
    "list_namespaced_deployment",
}
API_SHAPED = re.compile(r"namespace|cluster")
WRITE_PREFIX = re.compile(r"^(create|patch|replace|delete|connect|post|put|update)_")
# Generic ways to reach the API without a typed method name. (Bare replace/update are left out on
# purpose: str.replace, datetime.replace and dict.update are everyday Python.)
FORBIDDEN_NAMES = {"call_api", "request", "create", "patch", "delete", "post", "put"}
FORBIDDEN_IMPORTS = ("kubernetes.dynamic", "kubernetes.stream")


def violations(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "getattr":
                found.append(f"{node.lineno}: getattr")
            if isinstance(func, ast.Attribute):
                name = func.attr
                if WRITE_PREFIX.match(name) or name in FORBIDDEN_NAMES:
                    found.append(f"{node.lineno}: {name}")
                elif API_SHAPED.search(name) and name not in ALLOWED_API_CALLS:
                    found.append(f"{node.lineno}: {name} (not allowlisted)")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = {f"{module}.{a.name}" for a in node.names}
            for bad in FORBIDDEN_IMPORTS:
                if module.startswith(bad) or bad in names:
                    found.append(f"{node.lineno}: import {bad}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(FORBIDDEN_IMPORTS):
                    found.append(f"{node.lineno}: import {alias.name}")
    return found


def sources() -> list[Path]:
    files = sorted(SRC.rglob("*.py"))
    assert files, "no source files found"
    return files


def test_source_never_calls_write_methods():
    offenders = [f"{p.name}:{v}" for p in sources() for v in violations(p.read_text())]
    assert offenders == []


def test_source_kubernetes_api_calls_are_list_or_read():
    used = set()
    for path in sources():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if API_SHAPED.search(node.func.attr):
                    used.add(node.func.attr)
    assert used, "expected at least one Kubernetes API call"
    assert used <= ALLOWED_API_CALLS
    assert all(name.startswith(("list_", "read_")) for name in ALLOWED_API_CALLS)


def test_source_never_imports_exec_streaming():
    for path in sources():
        assert not [v for v in violations(path.read_text()) if "import" in v], path.name


def test_guard_flags_known_write_patterns():
    sample = """
import kubernetes.stream
from kubernetes import dynamic
from kubernetes.dynamic import DynamicClient
core.delete_namespaced_pod("p", "demo")
core.api_client.call_api("/api/v1/namespaces/demo/pods/p", "DELETE")
getattr(core, "delete_namespaced_pod")("p", "demo")
resource.delete(name="p", namespace="demo")
resource.patch(body={}, name="p")
client.request("DELETE", "/api/v1/namespaces/demo/pods/p")
core.list_namespaced_secret("demo")
"""
    found = violations(sample)
    assert len(found) == 10, found
    assert violations('core.list_namespaced_pod("demo")\n"a".replace("a", "b")\n') == []


def test_every_pod_log_read_is_bounded_by_lines_and_time():
    reads = []
    for path in sources():
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "read_namespaced_pod_log"
            ):
                reads.append((path.name, node.lineno, {k.arg for k in node.keywords}))
    assert reads, "expected at least one log read"
    for name, line, keywords in reads:
        assert {"tail_lines", "since_seconds"} <= keywords, f"{name}:{line} unbounded log read"
        assert "follow" not in keywords, f"{name}:{line} follows logs"
