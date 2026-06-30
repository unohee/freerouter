"""FastAPI 앱 — OpenAI 호환 프록시. 라우팅/폴백 코어는 FreeRouterClient를 재사용한다."""

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
    """앱 수명 동안 단일 httpx 클라이언트를 공유하는 FreeRouterClient를 둔다."""
    http = httpx.AsyncClient(timeout=settings.request_timeout)
    fr = FreeRouterClient(http_client=http)
    app.state.fr = fr
    try:
        await fr.models()  # 무료 목록 워밍업
    except Exception:  # noqa: BLE001 — 워밍업 실패해도 첫 요청에서 재시도하면 됨
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
    """라우팅 가능한 무료 모델만 OpenAI `/models` 형식으로 노출."""
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
    """OpenAI 호환 chat completions — 무료 모델 풀로 라우팅 + 폴백."""
    if not settings.openrouter_api_key:
        raise HTTPException(500, "OPENROUTER_API_KEY가 설정되지 않았습니다 (.env 확인).")

    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(400, f"잘못된 JSON 본문: {exc}") from exc

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
        # 비폴백 4xx(예: 잘못된 요청)는 업스트림 상태/본문을 그대로 전달.
        raise HTTPException(exc.response.status_code, exc.response.text[:500]) from exc

    return JSONResponse(
        data,
        headers={"X-freerouter-Model": str(data.get("model", ""))},
    )
