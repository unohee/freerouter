# freerouter — project context

## Purpose

An OpenAI-compatible proxy that routes **only OpenRouter's free models**. Its reason to exist is
to soften the main pain of the free tier (rate limits) by falling back across multiple free models.

## Stack

- Python >= 3.10, FastAPI + uvicorn (server), httpx (async upstream), pydantic-settings (config)
- src-layout: the package lives under `src/freerouter/`

## Structure

| File | Role |
|------|------|
| `config.py` | `.env`/env vars -> `Settings`. Global `settings`. |
| `models.py` | `is_free()`/`is_chat_capable()` predicates + `ModelRegistry` (free-list TTL cache). |
| `router.py` | `FreeRouter` — decides candidate order + tracks 429 cooldowns (pure; state = cooldown map). |
| `client.py` | `FreeRouterClient` — fallback-routing core (async `chat`/`complete`/`stream_raw` + sync wrappers). Defines `RETRYABLE_STATUS`. |
| `proxy.py` | FastAPI app: `/v1/chat/completions` (incl. streaming), `/v1/models`, `/health`. **Reuses client.py**. |
| `__main__.py` | uvicorn entry point (`freerouter` console script). |

**Two usage forms share the same core (`client.py`)**: the HTTP proxy server (`proxy.py`) and the
in-process library (`from freerouter import FreeRouterClient`). The fallback logic lives only in
`client.py`. There are no global singletons in `models.py`/`router.py` — state is per-instance, so
multiple clients can be used independently as a library.

## Key rules / gotchas

- **"Free" is decided by price** (`prompt == 0 && completion == 0`), not by the `:free` suffix —
  promo models (e.g. `openrouter/owl-alpha`) are free without the suffix, but they are pushed to a
  lower priority because they can demand credits (402).
- Fallback-triggering status codes live in `client.RETRYABLE_STATUS` (402/404/408/409/429/5xx).
- The model actually used is reported via the `X-freerouter-Model` response header.
- Attach OpenRouter's recommended identification headers (`HTTP-Referer`, `X-Title`) upstream.
- Never commit `.env` (it's gitignored). See `.env.example` for keys.

## Conventions

- **Code comments, docstrings, and docs are written in English.**

## Verification

- Unit tests: `pytest` (free filter / routing order / cooldown / client fallback via MockTransport).
- Live check: after starting the server, `curl /v1/models` for the free list and
  `/v1/chat/completions` for a round trip.
