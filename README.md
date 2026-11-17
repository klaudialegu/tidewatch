# Tidewatch

A read-only command-line tool that takes a snapshot of one app running on Kubernetes, diagnoses common failures from it, and writes a ticket-ready report: what broke, the evidence, and what to check next.

> **Status:** early (0.1.0), built and tested against a local [kind](https://kind.sigs.k8s.io/) lab cluster, not production. Snapshots and reports mask key-like tokens (minimal redaction), but names, hostnames and image references stay as they are: treat both as internal.

## Why

When an app on Kubernetes breaks, someone runs `kubectl get pods`, `describe`, `logs --previous` and `get events`, reads long outputs, and pieces together what happened. That's slow, easy to get wrong, and hard to hand over consistently.

Tidewatch does that first pass in one command and writes the result in a stable format a ticket, a runbook or another tool can use. It also catches a failure plain `kubectl` hides: an AI app whose pods are green while every call to its model provider fails.

## What it does

```
collect  ->  diagnose  ->  report
(read-only    (rules on      (terminal text, or JSON
 snapshot)     the snapshot)  with schema_version "1")
```

- **Collect:** lists pods, deployments and events in one namespace and reads bounded recent logs (the current run, plus the previous run after a restart). It saves them as a versioned JSON snapshot. It never changes the cluster and never reads Secrets.
- **Diagnose:** runs rules against the snapshot. Each finding has a severity, the object, evidence (the event, state or log line) and next steps.
- **Report:** readable text in the terminal, or JSON (`--json`, `-o report.json`).

Because diagnosis runs on a snapshot, you can scan offline: `tidewatch scan --from snapshot.json`. Log lines and messages in a snapshot are masked like report excerpts (see Safety).

### Rules

| Rule | Severity | Catches |
|---|---|---|
| `image-pull-failed` | critical | The image can't be pulled (wrong name or tag, missing registry access). |
| `container-exits-on-start` | critical | The container keeps exiting, with the log line that explains why (for example a missing setting). |
| `oom-killed` | critical | The container is killed for exceeding its memory limit. |
| `llm-upstream-auth-failed` | critical | The LLM provider rejects the credentials (401/403) while the pod looks healthy. |
| `llm-upstream-rate-limited` | warning | The LLM provider is rate-limiting (429). |
| `llm-upstream-timeout` | critical | Calls to the LLM provider time out. |

Severity reflects the snapshot. A container that's running again drops to warning (or info after a node or runtime restart), and an LLM finding drops one level when the latest upstream call succeeded. Tidewatch states facts and never calls anything "healthy".

## Example

A pod that Kubernetes shows as running and ready, while its model provider rejects every call (from a lab fixture):

```
$ tidewatch scan --from tests/fixtures/llm-bad-key.json
Tidewatch scan  kind-tidewatch-lab/demo  (1 critical, 0 warning, 0 info)

CRITICAL  LLM provider rejects the credentials  [llm-upstream-auth-failed]
  Pod/agent-fbdb89b9f-wphrk (container agent)
  Container agent logs the model provider rejecting the credentials (401/403) while Kubernetes shows it running and ready.
  Evidence:
    - 5 of 30 recent log lines (last 50 lines / last 60 min) show the model provider rejecting the credentials (401/403)
    - container agent: running, ready=true, 0 restarts
    - first seen 2026-11-03T02:44:24.328849171Z, last seen 2026-11-03T02:45:04.362453884Z
  Log excerpt:
    | 2026-11-03T02:44:44.346091833Z ERROR upstream model call failed: 401 Unauthorized (invalid API key)
    | 2026-11-03T02:44:54.352622713Z ERROR upstream model call failed: 401 Unauthorized (invalid API key)
    | 2026-11-03T02:45:04.362453884Z ERROR upstream model call failed: 401 Unauthorized (invalid API key)
  Next steps:
    1. Check the Secret holding the provider key: right key, right environment, not empty.
    2. Check whether the key was rotated or revoked on the provider side.
    3. After fixing the Secret, restart the Deployment so pods read the new value.
```

The same report as JSON (`--json`) has `schema_version`, `context`, `namespace`, `counts` and `findings`, each finding with `rule_id`, `severity`, `title`, `object`, `container`, `summary`, `evidence`, `log_excerpt` and `next_steps`. See [ADR 0003](docs/decisions/0003-findings-report-contract.md).

## Quick start

Needs [uv](https://docs.astral.sh/uv/) and Python 3.12. For the live commands you also need a kubeconfig context with read access to the namespace.

```sh
uv sync
uv run tidewatch scan --from tests/fixtures/memory.json          # offline, no cluster needed
uv run tidewatch scan --context <ctx> -n <namespace>             # live
uv run tidewatch scan --context <ctx> -n <namespace> --json -o report.json
uv run tidewatch collect --context <ctx> -n <namespace> -o snapshot.json
```

Narrow and bound what's read: `-l app=agent` (pods only), `--log-lines 50`, `--log-since-minutes 60` (the defaults are 50 lines and 60 minutes).

### The lab

`lab/` builds a local kind cluster and a small fake AI agent app with a fake model provider. Then it breaks them on purpose, so every rule can be seen working end to end. It needs Docker, kind and kubectl.

```sh
lab/cluster.sh up            # kind cluster "tidewatch-lab"
lab/patient.sh up            # the fake agent + fake model provider in namespace "demo"
lab/scenario.sh list         # failure scenarios: bad image, missing key, memory, LLM auth/rate limit/timeout
lab/scenario.sh apply-random # blind mode: diagnose it yourself, then `lab/scenario.sh reveal`
uv run tidewatch scan --context kind-tidewatch-lab -n demo
lab/scenario.sh reset
```

## Safety

- **Read-only:** the only Kubernetes calls are read the namespace (to check it exists), list pods, read pod logs, list deployments and list events. An allowlist test fails if any other call appears.
- **Bounded logs:** a capped number of recent lines per container. A finding's excerpt holds only the lines a rule matched (at most 3).
- **Masking (minimal redaction):** key-like tokens in every snapshot log line and message, and so in every report excerpt, (`sk-…`, Bearer and Basic auth, `api_key=…`, `"access_token": "…"`, long mixed-character strings; values of keys named like `token`, `secret` or `password`) become `***` (an `sk-` style prefix stays visible). Provider request IDs (`req_…`, `chatcmpl-…`) stay visible, because provider support asks for them. An older unmasked snapshot is masked when it's loaded. It's pattern-based: it catches common key shapes, not every secret, and object names, hostnames and image references stay as they are ([ADR 0005](docs/decisions/0005-minimal-redaction-in-snapshots.md)).
- **Lab data only:** every fixture in this repo comes from the local lab cluster.

## How it compares

Good tools already cover parts of this space. Tidewatch is deliberately narrow: one namespace, a snapshot, a short list of evidence-backed rules, and a stable JSON report for support tickets. The descriptions below are summaries, so check each project's docs for current details.

| Tool | What it is | Main difference from Tidewatch |
|---|---|---|
| [Replicated Troubleshoot](https://troubleshoot.sh/) | kubectl plugins (`support-bundle`, `preflight`) that collect a redacted support bundle and run analyzers on it, driven by YAML specs. | The closest match, and much broader: built-in redactors, many collectors and analyzers, specs vendors ship to customers. Tidewatch is a small rule set with no spec language, plus LLM-provider rules. |
| [k8sgpt](https://k8sgpt.ai/) | A CLI and an in-cluster operator that scan for problems with built-in analyzers and can explain them with an AI backend. | k8sgpt analyzes many resource types (by default across the cluster, with a namespace filter) and can explain with AI. Tidewatch explains from fixed rules with quoted evidence, can scan a saved snapshot offline, and has rules for upstream LLM failures that leave pods green. |
| [Popeye](https://popeyecli.com/) | A read-only cluster "sanitizer" that reports misconfigurations and gives the cluster a score. | Popeye finds configuration hygiene problems across a cluster. Tidewatch diagnoses why a specific app is failing right now. |
| [Robusta](https://home.robusta.dev/), [Komodor](https://komodor.com/) | Troubleshooting platforms that run in or alongside the cluster: alert enrichment, timelines, automations (Komodor is commercial). | Always-on platforms with an in-cluster component. Tidewatch is a one-shot CLI with nothing to install in the cluster. |
| [kube-score](https://github.com/zegl/kube-score), [Polaris](https://polaris.docs.fairwinds.com/) | Best-practice checks on manifests (Polaris also as a dashboard and admission controller). | They check configuration against best practices (kube-score on manifests; Polaris also on running workloads). Tidewatch diagnoses runtime failures from events, container states and logs. |

## Not in scope

Monitoring or alerting (it doesn't watch continuously), fixing anything (it never changes the cluster), a UI, and policy checks on manifests.

## Roadmap

Fuller redaction (names, hostnames, IPs), a shareable support bundle, runbook links per finding, a missing-Secret rule, and an optional AI summary that only ever sees findings, never raw logs.

## Development

```sh
uv run pytest                                # Tidewatch tests
(cd lab/agent && uv run pytest)              # the lab app's tests
uv run ruff check . && uv run ruff format --check .
```

Design decisions are recorded as ADRs in [docs/decisions/](docs/decisions/), and the plan is in [docs/PLAN.md](docs/PLAN.md).
