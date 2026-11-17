# 0005. Minimal redaction: snapshots are masked like reports
**Status:** Accepted
**Date:** 2026-11-17

## Context
Report excerpts already mask key-like tokens (ADR 0003 amendment), but a `collect` snapshot stored raw log lines and messages. Snapshots are the file people are most likely to attach to a ticket, and offline scans (`scan --from`) read them. The project rule was to stay private until redaction exists.

## Decision
Every log line (current and previous run) and every free-text message (container state messages, event messages) is masked with the same patterns as report excerpts, in the snapshot models themselves. So a snapshot written by `collect` is masked, and an older unmasked snapshot is masked as soon as it's loaded.

## Options considered
- Mask only in `collect`: older snapshot files would stay unmasked when scanned or re-saved.
- Mask only when writing the file: a live `scan` would still hold raw lines in memory and in any later output path.
- Full redaction (names, hostnames, IPs, image references): much larger, and it removes what support needs to act on. Later, if needed.
- Keep snapshots raw and only warn in the README: easy to miss; a leaked key travels with the file.

## Consequences
- Good: one place applies masking, so every path (collect, live scan, `scan --from`, re-saved files) gets it, and rules still match on masked lines (tested).
- Good: same shape, so snapshots stay at `schema_version` "1".
- Trade-off: pattern-based. It catches common key shapes, not every secret, and object names, hostnames and image references stay as they are.
- Trade-off: a masked value can't be recovered from the snapshot; to see it, read the logs in the cluster.
