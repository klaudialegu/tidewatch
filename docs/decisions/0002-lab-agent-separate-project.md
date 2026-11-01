# 0002. The lab's fake agent is its own project, outside Tidewatch's dependencies
**Status:** Accepted
**Date:** 2026-11-01

## Context
The lab needs a "patient": a small fake agent app that runs in the kind cluster and can be broken on purpose. It is written with FastAPI and uvicorn, which are not in Tidewatch's stack (docs/PLAN.md). Tidewatch is a read-only diagnostic CLI, and its dependency list is part of what users trust.

## Decision
The fake app lives in `lab/agent/` as a standalone uv project (`uv init --no-workspace`) with its own lockfile and Dockerfile. FastAPI and uvicorn only exist in that project and its image, and a test (`tests/test_lab_manifests.py::test_tidewatch_project_has_no_fastapi`) fails if they ever appear in Tidewatch's pyproject.toml or uv.lock.

## Options considered
- Add FastAPI to Tidewatch's dev dependencies: simpler single environment, but it blurs "lab tool" and "product", and it grows the lockfile of a tool that should stay small.
- A uv workspace member: shares one lockfile, so FastAPI would still land in Tidewatch's uv.lock.

## Consequences
- Good: Tidewatch's dependencies stay exactly the PLAN.md stack. The patient can change freely without touching the product.
- Good: the image contains only the agent's runtime dependencies (`uv sync --no-dev`).
- Trade-off: two environments. The agent's tests run separately (`cd lab/agent && uv run pytest`), and ruff from the root treats `tidewatch_agent` as a third-party import.
