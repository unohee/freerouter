"""무료 모델 레지스트리 — OpenRouter `/models`를 가져와 무료만 필터링/캐시한다."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

from .config import settings


@dataclass(frozen=True)
class FreeModel:
    """무료로 호출 가능한 OpenRouter 모델 한 개."""

    id: str
    name: str
    context_length: int


def is_free(model: dict) -> bool:
    """모델 dict가 무료인지 판정.

    OpenRouter 가격은 1토큰당 USD 문자열("0", "0.0000001" 등)로 온다.
    prompt·completion 둘 다 0이면 무료로 본다. `:free` 접미사 없는
    프로모 모델(예: openrouter/owl-alpha)도 이 기준으로 포착된다.
    """
    pricing = model.get("pricing") or {}

    def _zero(v: object) -> bool:
        try:
            return float(v) == 0.0  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False

    return _zero(pricing.get("prompt")) and _zero(pricing.get("completion"))


def is_chat_capable(model: dict) -> bool:
    """chat completions로 쓸 수 있는 모델인지(텍스트를 출력하는지) 판정.

    output_modalities에 "text"가 없으면(예: 오디오/이미지 생성 전용) chat 풀에서 제외한다.
    필드가 없으면 보수적으로 텍스트 모델로 간주한다(구버전 응답 호환).
    """
    arch = model.get("architecture") or {}
    out = arch.get("output_modalities")
    if not out:
        return True
    return "text" in out


@dataclass
class ModelRegistry:
    """무료 모델 목록을 TTL 캐시로 관리한다."""

    _models: list[FreeModel] = field(default_factory=list)
    _fetched_at: float = 0.0

    async def get(self, client: httpx.AsyncClient, *, force: bool = False) -> list[FreeModel]:
        """캐시된 무료 모델 목록을 반환. TTL 만료 시(또는 force) 새로 가져온다."""
        age = time.monotonic() - self._fetched_at
        if force or not self._models or age > settings.model_refresh_ttl:
            await self._refresh(client)
        return list(self._models)

    async def _refresh(self, client: httpx.AsyncClient) -> None:
        resp = await client.get(f"{settings.openrouter_base_url}/models")
        resp.raise_for_status()
        data = resp.json().get("data", [])
        free = [
            FreeModel(
                id=m["id"],
                name=m.get("name", m["id"]),
                context_length=int(m.get("context_length") or 0),
            )
            for m in data
            if is_free(m) and is_chat_capable(m)
        ]
        # 우선순위: `:free` 접미사(진짜 무료 티어)를 먼저, 그 안에서 컨텍스트 큰 순.
        # 접미사 없는 프로모/프리뷰 모델(owl-alpha, lyria 등)은 pricing이 0이어도
        # 실제론 크레딧을 요구(402)하는 경우가 있어 뒤로 보낸다.
        free.sort(key=lambda m: (m.id.endswith(":free"), m.context_length), reverse=True)
        self._models = free
        self._fetched_at = time.monotonic()
