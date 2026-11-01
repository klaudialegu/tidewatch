#!/usr/bin/env bash
# Create, delete or check the local kind cluster "tidewatch-lab".
# Safety: this script only ever touches the tidewatch-lab cluster and passes
# --context explicitly, so it never acts on whatever context is "current".
# The cluster name lives only here (passed with --name), not in kind-config.yaml.
# Note: "up" makes kind-tidewatch-lab your current kubectl context; "down" leaves none set.
set -euo pipefail

CLUSTER="tidewatch-lab"
CONTEXT="kind-${CLUSTER}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${HERE}/kind-config.yaml"

usage() {
  echo "usage: $0 up|down|status" >&2
  exit 2
}

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "error: $1 is not installed" >&2; exit 1; }
}

exists() {
  kind get clusters 2>/dev/null | grep -qx "${CLUSTER}"
}

[[ $# -eq 1 ]] || usage
need docker
need kind
need kubectl
docker info >/dev/null 2>&1 || { echo "error: Docker is not running (open Docker Desktop)" >&2; exit 1; }

case "$1" in
  up)
    if exists; then
      echo "${CLUSTER} already exists"
    else
      kind create cluster --name "${CLUSTER}" --config "${CONFIG}" --wait 120s
    fi
    kubectl --context "${CONTEXT}" get nodes
    echo "current kubectl context: $(kubectl config current-context 2>/dev/null || echo none)"
    ;;
  down)
    if exists; then
      kind delete cluster --name "${CLUSTER}"
    else
      echo "${CLUSTER} does not exist, nothing to delete"
    fi
    ;;
  status)
    if exists; then
      kubectl --context "${CONTEXT}" get nodes
      echo "current kubectl context: $(kubectl config current-context 2>/dev/null || echo none)"
    else
      echo "${CLUSTER} is not running (run: $0 up)"
      exit 1
    fi
    ;;
  *)
    usage
    ;;
esac
