#!/usr/bin/env bash
# Capture test fixtures from tidewatch-lab: one Tidewatch snapshot each for the healthy patient and
# every scenario, written to tests/fixtures/<name>.json. Lab only, fake data only.
# Each capture starts from a freshly rebuilt namespace, so its events belong to that capture alone.
# Safety: every kubectl call passes --context, and it refuses to run unless tidewatch-lab exists.
#
#   lab/capture-fixtures.sh            healthy + all scenarios
#   lab/capture-fixtures.sh no-key     just the named ones
set -euo pipefail

CLUSTER="tidewatch-lab"
CONTEXT="kind-${CLUSTER}"
NS="demo"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${HERE}/../tests/fixtures"
WAIT="${CAPTURE_WAIT_SECONDS:-60}"

if ! kind get clusters 2>/dev/null | grep -qx "${CLUSTER}"; then
  echo "error: cluster ${CLUSTER} does not exist (run: lab/cluster.sh up). Nothing was changed." >&2
  exit 1
fi

names=("$@")
[[ ${#names[@]} -gt 0 ]] || names=(healthy $("${HERE}/scenario.sh" list))
mkdir -p "${OUT}"
state="$(mktemp)"  # keep blind-mode history out of this
trap 'rm -f "${state}"' EXIT

for name in "${names[@]}"; do
  echo "== ${name}"
  "${HERE}/patient.sh" down >/dev/null 2>&1 || { echo "error: patient.sh down failed" >&2; exit 1; }
  "${HERE}/patient.sh" up >/dev/null 2>&1 || { echo "error: patient.sh up failed" >&2; exit 1; }
  if [[ "${name}" != "healthy" ]]; then
    SCENARIO_STATE="${state}" "${HERE}/scenario.sh" apply "${name}" >/dev/null
  fi
  sleep "${WAIT}"
  file="${OUT}/${name}.json"
  (cd "${HERE}/.." && uv run --quiet tidewatch collect --context "${CONTEXT}" -n "${NS}" -o "${file}")
  key=$(kubectl --context "${CONTEXT}" -n "${NS}" get secret agent-secret -o jsonpath='{.data.API_KEY}' | base64 -d)
  if grep -qF -f <(printf '%s\n' "${key}") "${file}"; then
    rm -f "${file}"
    echo "error: ${name}.json contained the lab API key; deleted it" >&2
    exit 1
  fi
done

echo "== back to healthy"
"${HERE}/patient.sh" down >/dev/null 2>&1 || { echo "error: patient.sh down failed" >&2; exit 1; }
"${HERE}/patient.sh" up >/dev/null 2>&1 || { echo "error: patient.sh up failed" >&2; exit 1; }
echo "done: ${names[*]}"
