"""A fake "agent" app for the Tidewatch lab: the patient that Tidewatch diagnoses.

Lab only. It has no real AI. When MODEL_URL is set it calls the lab's fake model provider
(tidewatch_agent.model_api, inside the cluster) for /chat, and a background upstream check logs
one line per call, in the style LLM SDKs use.

Failure levers exist on purpose: a missing API_KEY makes it exit at startup, MEMORY_HOG=1 makes
it grow memory until the container's limit kills it, and the fake provider's MODEL_MODE makes
upstream calls fail.
"""

import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

MIB = 1024 * 1024

app = FastAPI(title="tidewatch-agent")


class ChatRequest(BaseModel):
    message: str


class UpstreamError(Exception):
    """The model provider call failed. The message never contains the API key."""


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _describe_http_error(exc: urllib.error.HTTPError) -> str:
    if exc.code == 429:
        retry = exc.headers.get("Retry-After") if exc.headers else None
        return "429 Too Many Requests" + (f" (retry after {retry}s)" if retry else "")
    detail = ""
    try:
        detail = json.loads(exc.read() or b"{}").get("error", "")
    except (ValueError, AttributeError):
        pass
    reason = exc.reason if isinstance(exc.reason, str) else ""
    return f"{exc.code} {reason}".strip() + (f" ({detail})" if detail else "")


def call_model(
    message: str,
    url: str | None = None,
    timeout: float | None = None,
    opener: Callable = urllib.request.urlopen,
) -> str:
    """Ask the model provider for a reply. Without MODEL_URL, echo (local use)."""
    url = url or os.environ.get("MODEL_URL")
    if not url:
        return f"echo: {message}"
    timeout = timeout if timeout is not None else float(os.environ.get("MODEL_TIMEOUT", "5"))
    request = urllib.request.Request(
        url,
        data=json.dumps({"message": message}).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ.get('API_KEY', '')}",
        },
        method="POST",
    )
    try:
        with opener(request, timeout=timeout) as response:
            return json.loads(response.read())["reply"]
    except urllib.error.HTTPError as exc:
        raise UpstreamError(_describe_http_error(exc)) from None
    except TimeoutError:
        raise UpstreamError(f"timed out after {timeout:g}s") from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise UpstreamError(f"timed out after {timeout:g}s") from None
        raise UpstreamError(f"connection failed: {exc.reason}") from None


@app.post("/chat")
def chat(request: ChatRequest):
    try:
        return {"reply": call_model(request.message)}
    except UpstreamError as exc:
        return JSONResponse({"error": f"upstream model call failed: {exc}"}, status_code=502)


def upstream_check(log: Callable[[str], None] = print, **call_kwargs) -> bool:
    """One upstream call, one log line. Returns whether it succeeded."""
    try:
        call_model("ping", **call_kwargs)
    except UpstreamError as exc:
        log(f"ERROR upstream model call failed: {exc}")
        return False
    log("INFO upstream model ok")
    return True


def upstream_check_loop(interval_s: float) -> None:
    while True:
        upstream_check(lambda line: print(line, flush=True))
        time.sleep(interval_s)


def memory_hog_enabled(env: dict[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return env.get("MEMORY_HOG") == "1"


def hog_memory(
    held: list[bytearray],
    step_mib: int = 10,
    cap_mib: int = 1024,
    interval_s: float = 0.5,
) -> None:
    """Allocate step_mib chunks into `held` until cap_mib is reached.

    bytearray(n) is zero-filled, which writes every page, so the memory is really resident.
    bytes(n) would not be: the OS hands out untouched pages and RSS barely moves.
    """
    while (len(held) + 1) * step_mib <= cap_mib:
        held.append(bytearray(step_mib * MIB))
        if interval_s:
            time.sleep(interval_s)


def main() -> None:
    if not os.environ.get("API_KEY"):
        print("fatal: API_KEY is not set", file=sys.stderr)
        sys.exit(1)

    if memory_hog_enabled():
        held: list[bytearray] = []
        threading.Thread(target=hog_memory, args=(held,), daemon=True).start()

    if os.environ.get("MODEL_URL"):
        interval = float(os.environ.get("UPSTREAM_CHECK_SECONDS", "10"))
        threading.Thread(target=upstream_check_loop, args=(interval,), daemon=True).start()

    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
