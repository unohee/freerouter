"""FreeRouterClient fallback-routing unit tests — fake upstream via httpx.MockTransport."""

import json

import httpx
import pytest

from freerouter.client import FreeRouterClient, FreeRouterError

MODELS_BODY = {
    "data": [
        {
            "id": "a:free",
            "name": "A",
            "context_length": 1000,
            "pricing": {"prompt": "0", "completion": "0"},
            "architecture": {"output_modalities": ["text"]},
        },
        {
            "id": "b:free",
            "name": "B",
            "context_length": 500,
            "pricing": {"prompt": "0", "completion": "0"},
            "architecture": {"output_modalities": ["text"]},
        },
    ]
}


def _make_client(chat_handler):
    """Client whose /models returns a fixed body and /chat is handled by chat_handler."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json=MODELS_BODY)
        model = json.loads(request.content)["model"]
        calls.append(model)
        return chat_handler(model)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fr = FreeRouterClient(api_key="test-key", http_client=http)
    return fr, calls


async def test_chat_success_first_model():
    def chat(model):
        return httpx.Response(200, json={"model": model, "choices": [{"message": {"content": "ok"}}]})

    fr, calls = _make_client(chat)
    data = await fr.chat([{"role": "user", "content": "hi"}], model="auto")
    assert data["choices"][0]["message"]["content"] == "ok"
    assert calls[0] == "a:free"  # starts from the top priority


async def test_chat_falls_back_on_429():
    def chat(model):
        if model == "a:free":
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(200, json={"model": model, "choices": [{"message": {"content": "ok"}}]})

    fr, calls = _make_client(chat)
    data = await fr.chat([{"role": "user", "content": "hi"}], model="auto")
    assert data["model"] == "b:free"  # a fell back -> b succeeded
    assert calls == ["a:free", "b:free"]


async def test_all_fail_raises():
    def chat(model):
        return httpx.Response(429, json={"error": "rate limited"})

    fr, _ = _make_client(chat)
    with pytest.raises(FreeRouterError):
        await fr.chat([{"role": "user", "content": "hi"}], model="auto")


async def test_non_retryable_4xx_propagates():
    def chat(model):
        return httpx.Response(400, json={"error": "bad request"})

    fr, calls = _make_client(chat)
    with pytest.raises(httpx.HTTPStatusError):
        await fr.chat([{"role": "user", "content": "hi"}], model="auto")
    assert calls == ["a:free"]  # propagated immediately, no fallback


async def test_requested_model_first():
    def chat(model):
        return httpx.Response(200, json={"model": model, "choices": [{"message": {"content": "ok"}}]})

    fr, calls = _make_client(chat)
    await fr.chat([{"role": "user", "content": "hi"}], model="b:free")
    assert calls[0] == "b:free"  # requested model takes priority
