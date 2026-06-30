"""인프로세스 클라이언트 — 서버 없이 import해서 무료 모델 풀로 라우팅/폴백한다.

서버(`proxy.py`)와 라이브러리 사용자가 공유하는 폴백 코어가 여기 있다.
async 코어(`chat`/`complete`/`stream_raw`)와 sync 래퍼(`chat_sync`/`models_sync`)를 제공한다.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx

from .config import settings
from .models import FreeModel, ModelRegistry
from .router import FreeRouter

# 폴백을 유발하는 업스트림 상태코드.
# 402 = pricing이 0이어도 크레딧을 요구하는 프로모/프리뷰 모델 → 진짜 무료로 우회.
# 404 = 이 모델은 chat 엔드포인트로 제공되지 않음(게이팅/미지원) → 다음 모델로 우회.
# 408/409/429/5xx = rate limit / 일시적 오류.
RETRYABLE_STATUS = {402, 404, 408, 409, 429, 500, 502, 503, 504}


class FreeRouterError(RuntimeError):
    """모든 무료 모델 후보가 폴백 사유로 실패했을 때."""


class FreeRouterClient:
    """OpenRouter 무료 모델 풀로 라우팅하는 인프로세스 클라이언트.

    사용 예 (async):
        async with FreeRouterClient() as fr:
            data = await fr.chat([{"role": "user", "content": "hi"}])

    사용 예 (sync, 이벤트 루프 밖에서):
        fr = FreeRouterClient()
        data = fr.chat_sync([{"role": "user", "content": "hi"}])

    Parameters
    ----------
    api_key:
        OpenRouter 키. None이면 settings(.env)의 값을 쓴다.
    http_client:
        외부에서 공유할 httpx.AsyncClient. 주면 close 책임은 호출자에게 있다.
        None이면 첫 호출 시 내부 생성하고 aclose()/컨텍스트 종료 시 닫는다.
    registry / router:
        주입하면 상태(무료 목록 캐시·cooldown)를 공유. None이면 인스턴스 전용 생성.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        registry: ModelRegistry | None = None,
        router: FreeRouter | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.openrouter_api_key
        self._http = http_client
        self._owns_client = http_client is None
        self._registry = registry or ModelRegistry()
        self._router = router or FreeRouter()

    # ── 수명주기 ──────────────────────────────────────────────────────────

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=settings.request_timeout)
            self._owns_client = True
        return self._http

    async def aclose(self) -> None:
        """내부 생성한 httpx 클라이언트를 닫는다(외부 주입분은 건드리지 않음)."""
        if self._owns_client and self._http is not None:
            await self._http.aclose()
            self._http = None

    async def __aenter__(self) -> FreeRouterClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise FreeRouterError("OPENROUTER_API_KEY가 없습니다 (.env 또는 api_key 인자).")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": settings.http_referer,
            "X-Title": settings.x_title,
            "Content-Type": "application/json",
        }

    async def _candidates(self, http: httpx.AsyncClient, payload: dict) -> list[str]:
        free = await self._registry.get(http)
        if not free:
            raise FreeRouterError("사용 가능한 무료 모델이 없습니다.")
        return self._router.order(free, payload.get("model"))

    # ── async 코어 ────────────────────────────────────────────────────────

    async def models(self) -> list[FreeModel]:
        """라우팅 가능한 무료(=text 출력 가능) 모델 목록."""
        return await self._registry.get(self._client())

    async def complete(self, payload: dict) -> dict:
        """OpenAI chat completions payload(dict)를 무료 풀로 라우팅. 폴백 포함.

        반환은 OpenRouter 응답 dict 그대로(`["model"]`에 실제 사용 모델이 담긴다).
        모든 후보 실패 시 FreeRouterError, 비폴백 4xx는 httpx.HTTPStatusError.
        """
        return await self._complete_with(self._client(), payload)

    async def chat(self, messages: list[dict], *, model: str = "auto", **params) -> dict:
        """편의 래퍼 — messages/추가 파라미터로 payload를 조립해 complete() 호출."""
        return await self.complete({"messages": messages, "model": model, **params})

    async def stream_raw(self, payload: dict) -> AsyncIterator[bytes]:
        """스트리밍 — raw SSE bytes를 yield. 첫 바이트 전까지만 폴백 가능.

        모든 후보 실패 시 OpenAI 스트림 규약에 맞춘 에러 SSE를 1건 내보낸다.
        """
        http = self._client()
        candidates = await self._candidates(http, payload)
        last_error = "후보 모델이 없습니다."
        for model_id in candidates:
            body = {**payload, "model": model_id, "stream": True}
            async with http.stream(
                "POST",
                f"{settings.openrouter_base_url}/chat/completions",
                headers=self._headers(),
                json=body,
            ) as resp:
                if resp.status_code in RETRYABLE_STATUS:
                    self._router.penalize(model_id)
                    await resp.aread()
                    last_error = f"{model_id}: {resp.status_code}"
                    continue
                if resp.status_code >= 400:
                    await resp.aread()
                    err = {"error": {"message": resp.text[:300], "code": resp.status_code}}
                    yield f"data: {json.dumps(err)}\n\n".encode()
                    return
                async for chunk in resp.aiter_bytes():
                    yield chunk
                return
        err = {"error": {"message": f"모든 무료 모델 폴백 실패. 마지막: {last_error}", "code": 502}}
        yield f"data: {json.dumps(err)}\n\n".encode()

    async def _complete_with(self, http: httpx.AsyncClient, payload: dict) -> dict:
        candidates = await self._candidates(http, payload)
        last_error = "후보 모델이 없습니다."
        for model_id in candidates:
            resp = await http.post(
                f"{settings.openrouter_base_url}/chat/completions",
                headers=self._headers(),
                json={**payload, "model": model_id},
            )
            if resp.status_code in RETRYABLE_STATUS:
                self._router.penalize(model_id)
                last_error = f"{model_id}: {resp.status_code} {resp.text[:200]}"
                continue
            resp.raise_for_status()
            return resp.json()
        raise FreeRouterError(f"모든 무료 모델 폴백 실패. 마지막 오류: {last_error}")

    # ── sync 래퍼 ─────────────────────────────────────────────────────────
    # 이벤트 루프 밖(동기 스크립트)에서만 호출할 것. 매 호출 격리된 임시
    # httpx 클라이언트를 쓰므로 주입된 http_client/실행 중인 루프와 충돌하지 않는다.
    # (registry/router 상태는 self의 것을 공유한다.)

    def chat_sync(self, messages: list[dict], *, model: str = "auto", **params) -> dict:
        """chat()의 동기 버전. 실행 중인 이벤트 루프 안에서 호출하면 RuntimeError."""
        payload = {"messages": messages, "model": model, **params}

        async def _run() -> dict:
            async with httpx.AsyncClient(timeout=settings.request_timeout) as http:
                return await self._complete_with(http, payload)

        return asyncio.run(_run())

    def models_sync(self) -> list[FreeModel]:
        """models()의 동기 버전."""

        async def _run() -> list[FreeModel]:
            async with httpx.AsyncClient(timeout=settings.request_timeout) as http:
                return await self._registry.get(http)

        return asyncio.run(_run())
