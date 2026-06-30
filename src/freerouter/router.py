"""Routing strategy — order free models by priority, skipping ones in cooldown."""

from __future__ import annotations

import time

from .config import settings
from .models import FreeModel


class FreeRouter:
    """Decides the fallback order and tracks rate-limit cooldowns.

    Its only state is a mapping of model id -> cooldown expiry (monotonic time).
    """

    def __init__(self) -> None:
        self._cooldown_until: dict[str, float] = {}

    def penalize(self, model_id: str) -> None:
        """Put a model that hit 429 (etc.) into cooldown (excluded for a while)."""
        self._cooldown_until[model_id] = time.monotonic() + settings.cooldown_seconds

    def _available(self, model_id: str) -> bool:
        until = self._cooldown_until.get(model_id)
        return until is None or time.monotonic() >= until

    def order(self, free_models: list[FreeModel], requested: str | None) -> list[str]:
        """Build the order of model ids to try for this request.

        - If `requested` is a specific free model, put it first.
        - For "auto"/empty/unknown, follow the registry priority order.
        - Models in cooldown are pushed to the back; if every candidate is in
          cooldown, try them anyway.
        - Finally truncate to max_attempts.
        """
        ids = [m.id for m in free_models]
        ordered: list[str] = []

        if requested and requested != "auto" and requested in ids:
            ordered.append(requested)

        fresh = [mid for mid in ids if mid not in ordered and self._available(mid)]
        cooling = [mid for mid in ids if mid not in ordered and not self._available(mid)]
        ordered.extend(fresh)
        ordered.extend(cooling)

        if not ordered:
            ordered = ids
        return ordered[: settings.max_attempts]
