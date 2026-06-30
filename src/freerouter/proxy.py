"""FastAPI app — OpenAI-compatible proxy. Reuses FreeRouterClient for routing/fallback."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .client import FreeRouterClient, FreeRouterError
from .config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Hold a FreeRouterClient sharing one httpx client for the app's lifetime."""
    http = httpx.AsyncClient(timeout=settings.request_timeout)
    fr = FreeRouterClient(http_client=http)
    app.state.fr = fr
    try:
        await fr.models()  # warm up the free-model list
    except Exception:  # noqa: BLE001 — warmup failure is fine; first request retries
        pass
    try:
        yield
    finally:
        await http.aclose()


app = FastAPI(title="freerouter", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/v1/models")
async def list_models(request: Request) -> dict:
    """Expose only routable free models in the OpenAI `/models` shape."""
    fr: FreeRouterClient = request.app.state.fr
    free = await fr.models()
    return {
        "object": "list",
        "data": [
            {"id": m.id, "object": "model", "owned_by": "openrouter", "name": m.name}
            for m in free
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """OpenAI-compatible chat completions — route through the free pool with fallback."""
    if not settings.openrouter_api_key:
        raise HTTPException(500, "OPENROUTER_API_KEY is not set (check .env).")

    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(400, f"invalid JSON body: {exc}") from exc

    fr: FreeRouterClient = request.app.state.fr

    if payload.get("stream"):
        return StreamingResponse(
            fr.stream_raw(payload),
            media_type="text/event-stream",
        )

    try:
        data = await fr.complete(payload)
    except FreeRouterError as exc:
        raise HTTPException(502, str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        # Non-retryable 4xx (e.g. a bad request) — pass the upstream status/body through.
        raise HTTPException(exc.response.status_code, exc.response.text[:500]) from exc

    return JSONResponse(
        data,
        headers={"X-freerouter-Model": str(data.get("model", ""))},
    )
