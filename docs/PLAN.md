# Tidewatch: plan

## What
Tidewatch is a read-only Python CLI that scans an app running on Kubernetes, diagnoses common failures, and writes a ticket-ready report.

Pipeline: **collect → redact → diagnose → report** (versioned JSON). The CLI (`tidewatch scan`) and, later, other tools (such as Brain Dock) read the same JSON.

## Why
A public learning project showing hands-on Kubernetes and Docker troubleshooting and support tooling: diagnostics, runbooks and triage. Honest hands-on experience in a local lab, not production claims.

## Architecture
[ADR 0001](decisions/0001-library-json-contract-cli.md): a standalone library, a versioned JSON contract, and a thin CLI. Tidewatch never imports Brain Dock; Brain Dock may depend on it later.

## Stack
uv, Python 3.12, typer, rich, pydantic, the official kubernetes client, pytest, ruff.
Lab: Docker Desktop plus a kind cluster named `tidewatch-lab`.

## Safety
- **Strictly read-only against clusters:** get, list and log only, enforced by a test.
- **Lab data only:** never real company clusters or customer logs.
- **The repo stays private until redaction exists.**

## Roadmap
**Session 1** (stops at the end of Block 4: about 3.5 h of work plus a 30-minute break):
1. **Setup:** check or install docker, kind and kubectl; create the cluster; set up the Claude Code configuration (reviewer, /second-opinion, skills, hooks, settings); create a private GitHub repo.
2. **The patient:** a tiny FastAPI fake agent (`/health`, `/chat`; exits if `API_KEY` is missing; a `MEMORY_HOG` flag), with a Dockerfile, deployed to kind (namespace `demo`, Deployment, Service, Secret).
3. **Lab:** the scenarios bad-image, no-key and memory (64Mi limit), plus an apply/reset script.
4. **Collect:** a Snapshot model plus `tidewatch collect -n demo -o snapshot.json`; save fixtures for healthy and the 3 scenarios.

**Session 2:**
- **Diagnose:** a rules registry and 3 rules, tested against the fixtures (healthy = 0 findings).
- **`tidewatch scan`:** the report, plus `--json` with `schema_version`.
- **README.**

**Before going public:** minimal redaction (snapshots masked like reports, [ADR 0005](decisions/0005-minimal-redaction-in-snapshots.md)), then a final privacy check of the full history, then the owner's OK.

**Later:**
1. Fuller redaction (names, hostnames, IPs), `tidewatch bundle`, runbooks, and a bonus rule (missing Secret).
2. An AI summary step (framework to be decided; it gets findings only, never raw logs).
