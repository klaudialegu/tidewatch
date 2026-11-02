#!/usr/bin/env bash
# Break the lab patient on purpose (one scenario at a time), or put it back.
# Safety: every kubectl call passes --context kind-tidewatch-lab, and nothing happens unless the
# tidewatch-lab cluster exists. The patient must already be deployed (lab/patient.sh up).
#
#   lab/scenario.sh list            the scenario names
#   lab/scenario.sh apply <name>    apply one scenario by name
#   lab/scenario.sh apply-random    blind mode: apply one you haven't done yet, without naming it
#   lab/scenario.sh reveal          name the scenario that is currently applied (none after a reset)
#   lab/scenario.sh reset           back to the healthy patient (does not clear blind-mode history)
#   lab/scenario.sh restart-round   clear blind-mode history, so every scenario is "new" again
set -euo pipefail

CLUSTER="tidewatch-lab"
CONTEXT="kind-${CLUSTER}"
NS="demo"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCENARIOS=(bad-image no-key memory)
# Blind-mode memory: one used scenario per line, the last line is the current one (gitignored).
STATE="${SCENARIO_STATE:-${HERE}/.scenario-state}"

usage() {
  echo "usage: $0 list | apply <name> | apply-random | reveal | reset | restart-round" >&2
  exit 2
}

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "error: $1 is not installed" >&2; exit 1; }
}

k() {
  kubectl --context "${CONTEXT}" "$@"
}

known() {
  local s
  for s in "${SCENARIOS[@]}"; do [[ "$s" == "$1" ]] && return 0; done
  return 1
}

[[ $# -ge 1 ]] || usage
cmd="$1"
case "${cmd}" in
  list) [[ $# -eq 1 ]] || usage; printf '%s\n' "${SCENARIOS[@]}"; exit 0 ;;
  reveal)
    [[ $# -eq 1 ]] || usage
    last="$(tail -n 1 "${STATE}" 2>/dev/null || true)"
    if [[ -z "${last}" ]]; then
      echo "no scenario recorded"
    elif [[ "${last}" == "reset" ]]; then
      echo "no scenario applied (patient was reset to healthy)"
    else
      echo "current scenario: ${last}"
    fi
    exit 0
    ;;
  restart-round) [[ $# -eq 1 ]] || usage; rm -f "${STATE}"; echo "blind-mode history cleared"; exit 0 ;;
  apply) [[ $# -eq 2 ]] || usage; known "$2" || { echo "error: unknown scenario '$2' (see: $0 list)" >&2; exit 2; } ;;
  apply-random|reset) [[ $# -eq 1 ]] || usage ;;
  *) usage ;;
esac

need kind
need kubectl
if ! kind get clusters 2>/dev/null | grep -qx "${CLUSTER}"; then
  echo "error: cluster ${CLUSTER} does not exist (run: lab/cluster.sh up). Nothing was changed." >&2
  exit 1
fi
# The scenarios assume the patient as lab/patient.sh up leaves it (image loaded, Secret created).
if ! k -n "${NS}" get deployment/agent secret/agent-secret >/dev/null 2>&1; then
  echo "error: the patient is not deployed (run: lab/patient.sh up first). Nothing was changed." >&2
  exit 1
fi

apply_scenario() {
  # Output goes to /dev/null so blind mode never shows which objects changed.
  k apply -k "${HERE}/scenarios/$1" >/dev/null
}

case "${cmd}" in
  apply)
    apply_scenario "$2"
    echo "$2" >> "${STATE}"
    echo "scenario applied: $2"
    ;;
  apply-random)
    remaining=()
    for s in "${SCENARIOS[@]}"; do
      grep -qx "$s" "${STATE}" 2>/dev/null || remaining+=("$s")
    done
    if [[ ${#remaining[@]} -eq 0 ]]; then
      echo "every scenario has been used this round (run: $0 restart-round)" >&2
      exit 1
    fi
    pick="${remaining[RANDOM % ${#remaining[@]}]}"
    apply_scenario "${pick}"
    echo "${pick}" >> "${STATE}"
    echo "a scenario is applied (${#remaining[@]} were left this round)"
    ;;
  reset)
    k apply -k "${HERE}/k8s" >/dev/null
    k -n "${NS}" rollout status deployment/agent --timeout=120s
    echo "reset" >> "${STATE}"
    echo "patient reset to healthy"
    ;;
esac
