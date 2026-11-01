#!/usr/bin/env bash
# Deploy, check or remove the lab "patient" (the fake agent app) in namespace demo on tidewatch-lab.
# Safety: every kubectl call goes through k(), which always passes --context kind-tidewatch-lab,
# and nothing happens unless the tidewatch-lab cluster exists.
#
#   lab/patient.sh up       build the image, load it into kind, create the Secret, apply, wait
#   lab/patient.sh status   pods and service in demo
#   lab/patient.sh check    /health and /chat answer; logs don't contain the API key
#   lab/patient.sh down     delete namespace demo
set -euo pipefail

CLUSTER="tidewatch-lab"
CONTEXT="kind-${CLUSTER}"
NS="demo"
IMAGE="tidewatch-agent:0.1.0"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  echo "usage: $0 up|status|check|down" >&2
  exit 2
}

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "error: $1 is not installed" >&2; exit 1; }
}

k() {
  kubectl --context "${CONTEXT}" "$@"
}

[[ $# -eq 1 ]] || usage
case "$1" in up|status|check|down) ;; *) usage ;; esac

need docker
need kind
need kubectl
docker info >/dev/null 2>&1 || { echo "error: Docker is not running (open Docker Desktop)" >&2; exit 1; }
if ! kind get clusters 2>/dev/null | grep -qx "${CLUSTER}"; then
  echo "error: cluster ${CLUSTER} does not exist (run: lab/cluster.sh up). Nothing was changed." >&2
  exit 1
fi

case "$1" in
  up)
    need openssl
    # --provenance=false: kind can't load images with attestations from Docker's containerd store.
    docker build --provenance=false -t "${IMAGE}" "${HERE}/agent"
    kind load docker-image "${IMAGE}" --name "${CLUSTER}"
    k apply -f "${HERE}/k8s/namespace.yaml"
    if ! k -n "${NS}" get secret agent-secret >/dev/null 2>&1; then
      # A random fake key, passed through a file descriptor so it never appears in argv or output.
      k -n "${NS}" create secret generic agent-secret \
        --from-env-file=<(printf 'API_KEY=%s\n' "$(openssl rand -hex 16)") >/dev/null
      echo "secret/agent-secret created (random fake value, not shown)"
    fi
    k apply -f "${HERE}/k8s/deployment.yaml" -f "${HERE}/k8s/service.yaml"
    k -n "${NS}" rollout status deployment/agent --timeout=120s
    ;;
  status)
    k -n "${NS}" get pods,svc
    ;;
  check)
    port=18080
    k -n "${NS}" port-forward svc/agent "${port}:80" >/dev/null 2>&1 &
    pf=$!
    trap 'kill "${pf}" 2>/dev/null || true' EXIT
    for _ in $(seq 1 20); do
      curl -fsS "http://127.0.0.1:${port}/health" >/dev/null 2>&1 && break
      sleep 0.5
    done
    echo "health: $(curl -fsS "http://127.0.0.1:${port}/health")"
    echo "chat:   $(curl -fsS -X POST -H 'Content-Type: application/json' \
      -d '{"message":"ping"}' "http://127.0.0.1:${port}/chat")"
    key=$(k -n "${NS}" get secret agent-secret -o jsonpath='{.data.API_KEY}' | base64 -d)
    if k -n "${NS}" logs deployment/agent | grep -qF -f <(printf '%s\n' "${key}"); then
      echo "FAIL: the API key appears in the pod logs" >&2
      exit 1
    fi
    echo "logs:   API key not present"
    ;;
  down)
    k delete namespace "${NS}" --ignore-not-found --wait=true
    ;;
esac
