"""A fake "agent" app for the Tidewatch lab: the patient that Tidewatch diagnoses.

Lab only. It has no real AI and makes no network calls. Two failure levers exist on purpose:
a missing API_KEY makes it exit at startup, and MEMORY_HOG=1 makes it grow memory until the
container's memory limit kills it.
"""

import os
import sys
import threading
import time

from fastapi import FastAPI
from pydantic import BaseModel

MIB = 1024 * 1024

app = FastAPI(title="tidewatch-agent")


class ChatRequest(BaseModel):
    message: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat")
def chat(request: ChatRequest) -> dict[str, str]:
    return {"reply": f"echo: {request.message}"}


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

    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
