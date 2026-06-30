"""Unit tests for the free/chat-capable model predicates."""

from freerouter.models import is_chat_capable, is_free


def test_free_by_zero_pricing():
    assert is_free({"id": "x:free", "pricing": {"prompt": "0", "completion": "0"}})


def test_free_promo_without_suffix():
    # Free even without a :free suffix when pricing is 0 (e.g. promo models).
    assert is_free({"id": "openrouter/owl-alpha", "pricing": {"prompt": "0", "completion": "0"}})


def test_paid_prompt():
    assert not is_free({"id": "y", "pricing": {"prompt": "0.0000015", "completion": "0"}})


def test_paid_completion():
    assert not is_free({"id": "z", "pricing": {"prompt": "0", "completion": "0.000002"}})


def test_missing_pricing():
    assert not is_free({"id": "no-pricing"})


def test_malformed_pricing():
    assert not is_free({"id": "bad", "pricing": {"prompt": "free", "completion": "0"}})


def test_chat_capable_text_output():
    assert is_chat_capable({"architecture": {"output_modalities": ["text"]}})


def test_chat_capable_excludes_audio_only():
    assert not is_chat_capable({"architecture": {"output_modalities": ["audio"]}})


def test_chat_capable_includes_text_among_multi():
    assert is_chat_capable({"architecture": {"output_modalities": ["text", "audio"]}})


def test_chat_capable_defaults_true_when_missing():
    # No architecture/output_modalities -> conservatively treat as a text model.
    assert is_chat_capable({"id": "legacy"})
