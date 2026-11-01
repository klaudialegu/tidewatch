# 0001. A standalone library, a versioned JSON contract, a thin CLI
**Status:** Accepted
**Date:** 2026-11-01

## Context
Tidewatch diagnoses apps running on Kubernetes and writes a ticket-ready report. The same results should serve a person at a terminal today and other tools later (for example Brain Dock, a separate local app). If the diagnosis logic lived inside the CLI, or inside Brain Dock, every new consumer would mean copying or coupling code.

## Decision
- **A standalone library** (`tidewatch`) holds the pipeline: collect → redact → diagnose → report.
- **A versioned JSON contract:** the report is a JSON document with a `schema_version`, defined with pydantic models. A breaking change means a new version.
- **A thin CLI** (typer + rich): it parses arguments, calls the library and prints. No logic lives only in the CLI.
- **Dependency direction:** Tidewatch never imports Brain Dock. Brain Dock may depend on Tidewatch, or only read its JSON, later.

## Options considered
- **A Brain Dock module:** quicker to show inside one app, but it ties a Kubernetes tool to a separate personal app, and Brain Dock's modules may not reach outside the machine.
- **A CLI with no stable output:** simplest now, but every consumer would scrape terminal text.
- **A web service:** more moving parts than a local, read-only diagnostic tool needs.

## Consequences
- Good: one implementation, many consumers; the JSON contract is testable on its own, with fixtures.
- Good: the CLI stays small, so tests target the library.
- Trade-off: the schema needs versioning discipline from day one (`schema_version`, and tests that load old fixtures).
