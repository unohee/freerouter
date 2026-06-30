"""FreeRouterClient 폴백 라우팅 단위테스트 — httpx.MockTransport로 업스트림 가짜 응답."""

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
    """models는 고정 응답, chat은 주어진 핸들러로 처리하는 MockTransport 클라이언트."""
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
    assert calls[0] == "a:free"  # 우선순위 1순위부터


async def test_chat_falls_back_on_429():
    def chat(model):
        if model == "a:free":
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(200, json={"model": model, "choices": [{"message": {"content": "ok"}}]})

    fr, calls = _make_client(chat)
    data = await fr.chat([{"role": "user", "content": "hi"}], model="auto")
    assert data["model"] == "b:free"  # a 폴백 → b 성공
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
    assert calls == ["a:free"]  # 폴백 없이 즉시 전파


async def test_requested_model_first():
    def chat(model):
        return httpx.Response(200, json={"model": model, "choices": [{"message": {"content": "ok"}}]})

    fr, calls = _make_client(chat)
    await fr.chat([{"role": "user", "content": "hi"}], model="b:free")
    assert calls[0] == "b:free"  # 지정 모델 우선
