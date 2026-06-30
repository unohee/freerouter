"""In-process client — route through the free-model pool without running a server.

This holds the fallback core shared by the proxy server (`proxy.py`) and library
users. It exposes an async core (`chat`/`complete`/`stream_raw`) plus sync wrappers
(`chat_sync`/`models_sync`).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx

from .config import settings
from .models import FreeModel, ModelRegistry
from .router import FreeRouter

# Upstream status codes that trigger a fallback.
# 402 = promo/preview model that demands credits despite 0 pricing -> route to a real free one.
# 404 = model not served on the chat endpoint (gated/unsupported) -> try the next one.
# 408/409/429/5xx = rate limit / transient errors.
RETRYABLE_STATUS = {402, 404, 408, 409, 429, 500, 502, 503, 504}


class FreeRouterError(RuntimeError):
    """Raised when every free-model candidate failed for a fallback reason."""


class FreeRouterClient:
    """In-process client that routes through OpenRouter's free-model pool.

    Async usage:
        async with FreeRouterClient() as fr:
            data = await fr.chat([{"role": "user", "content": "hi"}])

    Sync usage (outside an event loop):
        fr = FreeRouterClient()
        data = fr.chat_sync([{"role": "user", "content": "hi"}])

    Parameters
    ----------
    api_key:
        OpenRouter key. Falls back to settings (.env) when None.
    http_client:
        A shared httpx.AsyncClient. If provided, the caller owns its lifecycle.
        If None, one is created lazily on first use and closed on aclose()/exit.
    registry / router:
        Inject to share state (free-list cache / cooldowns). Created per instance
        when None.
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

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=settings.request_timeout)
            self._owns_client = True
        return self._http

    async def aclose(self) -> None:
        """Close the internally created httpx client (leaves an injected one alone)."""
        if self._owns_client and self._http is not None:
            await self._http.aclose()
            self._http = None

    async def __aenter__(self) -> FreeRouterClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ── Internal helpers ──────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise FreeRouterError("OPENROUTER_API_KEY is missing (.env or api_key arg).")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": settings.http_referer,
            "X-Title": settings.x_title,
            "Content-Type": "application/json",
        }

    async def _candidates(self, http: httpx.AsyncClient, payload: dict) -> list[str]:
        free = await self._registry.get(http)
        if not free:
            raise FreeRouterError("No free models available.")
        return self._router.order(free, payload.get("model"))

    # ── Async core ────────────────────────────────────────────────────────

    async def models(self) -> list[FreeModel]:
        """The list of routable free (text-capable) models."""
        return await self._registry.get(self._client())

    async def complete(self, payload: dict) -> dict:
        """Route an OpenAI chat-completions payload (dict) through the free pool.

        Returns the OpenRouter response dict as-is (`["model"]` holds the model
        actually used). Raises FreeRouterError if all candidates fail, or
        httpx.HTTPStatusError on a non-retryable 4xx.
        """
        return await self._complete_with(self._client(), payload)

    async def chat(self, messages: list[dict], *, model: str = "auto", **params) -> dict:
        """Convenience wrapper — build a payload from messages/params, call complete()."""
        return await self.complete({"messages": messages, "model": model, **params})

    async def stream_raw(self, payload: dict) -> AsyncIterator[bytes]:
        """Stream raw SSE bytes. Fallback is only possible before the first byte.

        If all candidates fail, emit a single error SSE in the OpenAI stream shape.
        """
        http = self._client()
        candidates = await self._candidates(http, payload)
        last_error = "no candidate models"
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
        err = {"error": {"message": f"all free models failed; last: {last_error}", "code": 502}}
        yield f"data: {json.dumps(err)}\n\n".encode()

    async def _complete_with(self, http: httpx.AsyncClient, payload: dict) -> dict:
        candidates = await self._candidates(http, payload)
        last_error = "no candidate models"
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
        raise FreeRouterError(f"all free models failed; last error: {last_error}")

    # ── Sync wrappers ─────────────────────────────────────────────────────
    # Call these only outside an event loop (sync scripts). Each call uses an
    # isolated, temporary httpx client, so it never conflicts with an injected
    # http_client or a running loop. (registry/router state is still shared.)

    def chat_sync(self, messages: list[dict], *, model: str = "auto", **params) -> dict:
        """Synchronous version of chat(). Raises RuntimeError inside a running loop."""
        payload = {"messages": messages, "model": model, **params}

        async def _run() -> dict:
            async with httpx.AsyncClient(timeout=settings.request_timeout) as http:
                return await self._complete_with(http, payload)

        return asyncio.run(_run())

    def models_sync(self) -> list[FreeModel]:
        """Synchronous version of models()."""

        async def _run() -> list[FreeModel]:
            async with httpx.AsyncClient(timeout=settings.request_timeout) as http:
                return await self._registry.get(http)

        return asyncio.run(_run())
