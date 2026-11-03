"""A fake LLM provider for the Tidewatch lab.

Lab only: no real model, no real key, no outside network.

MODEL_MODE picks its behaviour (read per request, so a restart isn't needed in tests):
  ok          200 with a canned reply
  reject-key  401 {"error": "invalid API key"}
  rate-limit  429 with Retry-After: 30
  slow        waits MODEL_SLOW_SECONDS (default 30) before answering, without blocking the server
"""

import asyncio
import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="tidewatch-model")


class ChatRequest(BaseModel):
    message: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/chat")
async def chat(request: ChatRequest):
    mode = os.environ.get("MODEL_MODE", "ok")
    if mode == "reject-key":
        return JSONResponse({"error": "invalid API key"}, status_code=401)
    if mode == "rate-limit":
        return JSONResponse(
            {"error": "rate limit exceeded"}, status_code=429, headers={"Retry-After": "30"}
        )
    if mode == "slow":
        await asyncio.sleep(float(os.environ.get("MODEL_SLOW_SECONDS", "30")))
    return {"reply": f"model: {request.message}"}


def main() -> None:
    import uvicorn

    # No access log: its lines carry status codes and client ports, which aren't upstream errors.
    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False)
