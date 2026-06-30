"""FastAPI 앱 — OpenAI 호환 프록시. 무료 모델 풀로 라우팅하고 실패 시 폴백한다."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .config import settings
from .models import registry
from .router import router

# 폴백을 유발하는 업스트림 상태코드.
# 402 = pricing이 0이어도 크레딧을 요구하는 프로모/프리뷰 모델 → 진짜 무료로 우회.
# 404 = 이 모델은 chat 엔드포인트로 제공되지 않음(게이팅/미지원) → 다음 모델로 우회.
# 408/409/429/5xx = rate limit / 일시적 오류.
RETRYABLE_STATUS = {402, 404, 408, 409, 429, 500, 502, 503, 504}


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "HTTP-Referer": settings.http_referer,
        "X-Title": settings.x_title,
        "Content-Type": "application/json",
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 수명 동안 단일 AsyncClient를 공유하고, 시작 시 무료 목록을 워밍업한다."""
    client = httpx.AsyncClient(timeout=settings.request_timeout)
    app.state.client = client
    try:
        await registry.get(client, force=True)
    except Exception:  # noqa: BLE001 — 워밍업 실패해도 첫 요청에서 재시도하면 됨
        pass
    try:
        yield
    finally:
        await client.aclose()


app = FastAPI(title="freerouter", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/v1/models")
async def list_models(request: Request) -> dict:
    """라우팅 가능한 무료 모델만 OpenAI `/models` 형식으로 노출."""
    client: httpx.AsyncClient = request.app.state.client
    free = await registry.get(client)
    return {
        "object": "list",
        "data": [
            {"id": m.id, "object": "model", "owned_by": "openrouter", "name": m.name}
            for m in free
        ],
    }


async def _forward_once(
    client: httpx.AsyncClient, payload: dict, model_id: str
) -> httpx.Response:
    body = {**payload, "model": model_id}
    return await client.post(
        f"{settings.openrouter_base_url}/chat/completions",
        headers=_auth_headers(),
        json=body,
    )


async def _stream_with_fallback(
    client: httpx.AsyncClient, payload: dict, candidates: list[str]
) -> AsyncIterator[bytes]:
    """후보를 순서대로 시도하며 스트리밍. 첫 바이트를 보내기 전까지만 폴백 가능.

    상태코드가 폴백 사유면 해당 모델을 cooldown 시키고 다음으로 넘어간다.
    모두 실패하면 OpenAI 스트림 규약에 맞춘 에러 SSE를 1건 내보낸다.
    """
    last_error = "후보 모델이 없습니다."
    for model_id in candidates:
        body = {**payload, "model": model_id}
        async with client.stream(
            "POST",
            f"{settings.openrouter_base_url}/chat/completions",
            headers=_auth_headers(),
            json=body,
        ) as resp:
            if resp.status_code in RETRYABLE_STATUS:
                router.penalize(model_id)
                await resp.aread()
                last_error = f"{model_id}: {resp.status_code}"
                continue
            if resp.status_code >= 400:
                await resp.aread()
                err = {"error": {"message": resp.text[:300], "code": resp.status_code}}
                yield f"data: {json.dumps(err)}\n\n".encode()
                return
            # 정상 — 이 시점부터는 폴백 불가(바이트가 클라이언트로 흘러감).
            async for chunk in resp.aiter_bytes():
                yield chunk
            return

    err = {"error": {"message": f"모든 무료 모델 폴백 실패. 마지막: {last_error}", "code": 502}}
    yield f"data: {json.dumps(err)}\n\n".encode()


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """OpenAI 호환 chat completions — 무료 모델 풀로 라우팅 + 폴백."""
    if not settings.openrouter_api_key:
        raise HTTPException(500, "OPENROUTER_API_KEY가 설정되지 않았습니다 (.env 확인).")

    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(400, f"잘못된 JSON 본문: {exc}") from exc

    client: httpx.AsyncClient = request.app.state.client
    free = await registry.get(client)
    if not free:
        raise HTTPException(503, "사용 가능한 무료 모델이 없습니다.")

    requested = payload.get("model")
    candidates = router.order(free, requested)
    stream = bool(payload.get("stream"))

    if stream:
        # 폴백 루프를 제너레이터 내부에서 처리(첫 바이트 전까지 모델 교체 가능).
        return StreamingResponse(
            _stream_with_fallback(client, payload, candidates),
            media_type="text/event-stream",
        )

    last_error: str = "후보 모델이 없습니다."
    for model_id in candidates:
        resp = await _forward_once(client, payload, model_id)
        if resp.status_code in RETRYABLE_STATUS:
            router.penalize(model_id)
            last_error = f"{model_id}: {resp.status_code} {resp.text[:200]}"
            continue
        return JSONResponse(
            resp.json(),
            status_code=resp.status_code,
            headers={"X-freerouter-Model": model_id},
        )

    # 모든 후보가 폴백 사유로 실패.
    raise HTTPException(502, f"모든 무료 모델 폴백 실패. 마지막 오류: {last_error}")
