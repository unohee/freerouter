"""라우팅 순서·cooldown 단위테스트."""

from freerouter.config import settings
from freerouter.models import FreeModel
from freerouter.router import FreeRouter

FREE = [
    FreeModel(id="a:free", name="A", context_length=1_000_000),
    FreeModel(id="b:free", name="B", context_length=256_000),
    FreeModel(id="c:free", name="C", context_length=128_000),
]


def test_order_follows_registry_priority_on_auto():
    r = FreeRouter()
    order = r.order(FREE, "auto")
    assert order[0] == "a:free"  # 컨텍스트 큰 모델이 레지스트리에서 먼저 정렬됨


def test_requested_model_goes_first():
    r = FreeRouter()
    order = r.order(FREE, "c:free")
    assert order[0] == "c:free"


def test_unknown_requested_falls_back_to_priority():
    r = FreeRouter()
    order = r.order(FREE, "not-a-free-model")
    assert order[0] == "a:free"


def test_penalized_model_pushed_back():
    r = FreeRouter()
    r.penalize("a:free")
    order = r.order(FREE, "auto")
    assert order[-1] == "a:free"  # cooldown 모델은 뒤로


def test_max_attempts_truncates():
    r = FreeRouter()
    original = settings.max_attempts
    settings.max_attempts = 2
    try:
        assert len(r.order(FREE, "auto")) == 2
    finally:
        settings.max_attempts = original
