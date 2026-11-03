# 0004. LLM provider failures, recent logs, and additive snapshot fields
**Status:** Accepted
**Date:** 2026-11-02

## Context
The most common failure of an AI app on Kubernetes isn't a crash: the pod is Running, Ready and never restarted, but every call to the model provider fails (the key is rejected, the provider rate-limits, or calls time out). kubectl looks green; only the app's own logs show it. Tidewatch so far only read logs from a container's previous run, after a restart. Catching these failures means reading the current run's logs, and adding fields to the snapshot.

## Decision
- The lab gets a fake model provider (in-cluster, no real provider or key) and three scenarios: llm-bad-key, llm-rate-limit, llm-timeout.
- The snapshot gains optional fields: per-container `recent_logs` (`<RFC3339 timestamp> <message>` lines), pod `labels`, and the `selector` and log limits it was collected with. `collect`/`scan` accept `-l/--selector`, `--log-lines` and `--log-since-minutes`.
- Every log read is bounded by both a line count and a time window (defaults 50 lines, 60 minutes), and never follows; a test enforces this.
- **Schema policy:** adding an optional field keeps `schema_version` "1" (old snapshots still load, old readers ignore it). Renaming, removing or changing the meaning of a field needs "2".
- Rules never select pods by name. A pod gets a finding only from its own evidence; `--selector` narrows the pods by label.

## Options considered
- Bump to schema "2" for the new fields: correct but noisy; nothing about the existing fields changed.
- Read logs only after restarts (as before): misses exactly the green-but-broken failures this slice is about.
- Unbounded log reads: simplest, but slow on chatty apps and collects far more than diagnosis needs.
- Hard-code which pod is "the app": works for the lab, useless anywhere else.

## Consequences
- Good: Tidewatch can show "kubectl is green but the AI feature is down" with evidence, which is the support case these apps actually produce.
- Trade-off: snapshots now routinely hold current app logs from every container. Logs can contain anything an app prints (provider errors often echo part of a key), so this **raises the bar for redaction before the repo or any report goes public**. Until redaction exists, snapshots and reports stay inside the team.
