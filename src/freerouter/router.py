"""라우팅 전략 — 무료 모델을 우선순위 순으로 고르고, cooldown 중인 것은 건너뛴다."""

from __future__ import annotations

import time

from .config import settings
from .models import FreeModel


class FreeRouter:
    """폴백 후보 순서를 결정하고 rate-limit cooldown을 추적한다.

    상태는 모델 id → cooldown 만료 시각(monotonic) 매핑 하나뿐이다.
    """

    def __init__(self) -> None:
        self._cooldown_until: dict[str, float] = {}

    def penalize(self, model_id: str) -> None:
        """429 등을 맞은 모델을 cooldown 시킨다(일정 시간 후보에서 제외)."""
        self._cooldown_until[model_id] = time.monotonic() + settings.cooldown_seconds

    def _available(self, model_id: str) -> bool:
        until = self._cooldown_until.get(model_id)
        return until is None or time.monotonic() >= until

    def order(self, free_models: list[FreeModel], requested: str | None) -> list[str]:
        """이번 요청에서 시도할 모델 id 순서를 만든다.

        - requested가 구체적인 무료 모델이면 맨 앞에 둔다.
        - "auto"/빈 값/미보유 모델이면 레지스트리 우선순위를 따른다.
        - cooldown 중인 모델은 뒤로 밀되, 후보가 전부 cooldown이면 그대로 시도한다.
        - 최종적으로 max_attempts 개로 자른다.
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
