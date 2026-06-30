"""Free-model registry — fetches OpenRouter `/models` and filters/caches the free ones."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

from .config import settings


@dataclass(frozen=True)
class FreeModel:
    """A single OpenRouter model callable for free."""

    id: str
    name: str
    context_length: int


def is_free(model: dict) -> bool:
    """Decide whether a model dict is free.

    OpenRouter pricing comes as USD-per-token strings ("0", "0.0000001", ...).
    A model is treated as free when both prompt and completion price are 0.
    This also catches promo models without a `:free` suffix
    (e.g. openrouter/owl-alpha).
    """
    pricing = model.get("pricing") or {}

    def _zero(v: object) -> bool:
        try:
            return float(v) == 0.0  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False

    return _zero(pricing.get("prompt")) and _zero(pricing.get("completion"))


def is_chat_capable(model: dict) -> bool:
    """Decide whether a model can be used for chat completions (outputs text).

    If output_modalities lacks "text" (e.g. audio/image generation only), the
    model is excluded from the chat pool. When the field is missing, assume it
    is a text model (compat with older response shapes).
    """
    arch = model.get("architecture") or {}
    out = arch.get("output_modalities")
    if not out:
        return True
    return "text" in out


@dataclass
class ModelRegistry:
    """Manages the free-model list as a TTL cache."""

    _models: list[FreeModel] = field(default_factory=list)
    _fetched_at: float = 0.0

    async def get(self, client: httpx.AsyncClient, *, force: bool = False) -> list[FreeModel]:
        """Return the cached free-model list. Re-fetch when the TTL expires (or force)."""
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
        # Priority: `:free`-suffixed models (the real free tier) first, then by
        # larger context. Promo/preview models without the suffix can demand
        # credits (402) despite 0 pricing, so push them to the back.
        free.sort(key=lambda m: (m.id.endswith(":free"), m.context_length), reverse=True)
        self._models = free
        self._fetched_at = time.monotonic()
