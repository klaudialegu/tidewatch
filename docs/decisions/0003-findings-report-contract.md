# 0003. The findings report contract
**Status:** Accepted
**Date:** 2026-11-02

## Context
`tidewatch scan` turns a snapshot into findings that people paste into tickets and that other tools (a future AI summary step, Brain Dock) read. ADR 0001 made the JSON output a versioned contract. This ADR fixes what a finding contains, so the contract stays stable and safe to pass on.

## Decision
A report (`schema_version: "1"`) holds the source (context, namespace, snapshot time), counts by severity, and findings sorted critical > warning > info. Each finding has a rule ID, severity, title, the affected object and container, a one-line summary, **evidence** (facts the rule derived, at most 5), a separate **log_excerpt** (raw log lines, at most 3), and next steps.

## Options considered
- Raw log lines inside evidence: simpler, but redaction and the AI summary step ("findings only, never raw logs") couldn't drop them without a schema change.
- Free-text findings only: easy to read, impossible for tools to rely on.
- Severity from the rule alone: restart-based rules would stay critical forever after one crash, because Kubernetes keeps the last run's status for the pod's whole life.

## Consequences
- Good: evidence holds no raw log lines, and `log_excerpt` is a single field that redaction or an AI step can drop.
- Trade-off: evidence can still contain object names, image references and event messages (which may include internal hostnames). Reports aren't safe to share outside the team until redaction exists.
- Good: severity reflects the snapshot: critical while a container is down, warning once it runs again (stated as facts, never "healthy"), info for node or runtime restarts.
- Trade-off: any change to these fields means `schema_version` "2" and a reader that understands both.

## Amendment (2026-11-02, ADR 0004 slice)
`log_excerpt` holds **only the lines a rule matched**, never the lines around them (an empty excerpt is fine), and key-like tokens in it (`sk-...`, `Bearer ...`, `api_key=...`, long token strings) are masked to `***`. This narrows the field's content without changing its shape, so the report stays at `schema_version` "1". Masking is a guard rail, not redaction.
