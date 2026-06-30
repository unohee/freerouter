# freerouter

[![CI](https://github.com/unohee/freerouter/actions/workflows/ci.yml/badge.svg)](https://github.com/unohee/freerouter/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An OpenAI-compatible proxy that gathers **only OpenRouter's free model endpoints** and
routes/falls back across them automatically.

Point any existing OpenAI SDK/client at freerouter, and it picks a model from OpenRouter's
free pool (`pricing.prompt == 0 && pricing.completion == 0`), then **falls back to the next
free model automatically** on a rate limit (429) or a transient error.

## How it works

1. On startup, fetch OpenRouter `/api/v1/models`, **keep only free models**, and cache them
   (TTL 600s by default).
2. On `POST /v1/chat/completions`, the router builds a candidate order.
   - A specific free `model` goes first; otherwise (`auto`/empty/paid/unknown) the free-pool
     priority is used (`:free`-suffixed first, then larger context).
   - A model that just returned 429 is pushed back during its cooldown (60s by default).
3. Try candidates in order, falling back on 429/402/404/5xx. If all fail, return 502.
4. The response header `X-freerouter-Model` carries the model actually used.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # fill in OPENROUTER_API_KEY
```

## Run

```bash
freerouter            # or: python -m freerouter
# defaults to http://127.0.0.1:8000
```

## Usage (with the OpenAI SDK as-is)

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="unused")

resp = client.chat.completions.create(
    model="auto",  # freerouter picks from the free pool automatically
    messages=[{"role": "user", "content": "hello"}],
)
print(resp.choices[0].message.content)
```

curl:

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"hi"}]}'
```

## Use as a library (no server)

You can embed freerouter into another agent program **as a submodule** without running an
HTTP server. `FreeRouterClient` fetches the free-model list over REST (TTL-cached) and routes
with fallback in-process.

```python
import asyncio
from freerouter import FreeRouterClient

async def main():
    async with FreeRouterClient() as fr:           # uses OPENROUTER_API_KEY from .env
        free = await fr.models()                    # routable free models
        data = await fr.chat(
            [{"role": "user", "content": "hello"}],
            model="auto",                           # auto = pick from the free pool + fallback
            max_tokens=64,
        )
        print(data["model"], data["choices"][0]["message"]["content"])

asyncio.run(main())
```

In synchronous code (outside an event loop), use the sync wrapper:

```python
from freerouter import FreeRouterClient

fr = FreeRouterClient(api_key="sk-or-...")          # passing the key directly also works
data = fr.chat_sync([{"role": "user", "content": "hi"}], model="auto")
```

- **Share an httpx client**: if the agent already uses an `httpx.AsyncClient`, inject it —
  `FreeRouterClient(http_client=my_client)`. When injected, the caller owns its lifecycle.
- **Share state**: inject `registry`/`router` so several clients share the free-list cache and
  cooldowns.
- **Streaming**: `async for chunk in fr.stream_raw(payload)` — raw SSE bytes.
- A non-retryable 4xx (e.g. a bad request) raises `httpx.HTTPStatusError`; all-candidates-failed
  raises `FreeRouterError`.

> The same routing/fallback core is shared by the proxy server (`proxy.py`) and the library.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/chat/completions` | OpenAI-compatible. Supports streaming (`"stream": true`). |
| GET | `/v1/models` | Exposes only routable free models. |
| GET | `/health` | Health check. |

## Configuration

Controlled via `.env` or environment variables. See `src/freerouter/config.py` for the full list.

| Key | Default | Description |
|-----|---------|-------------|
| `OPENROUTER_API_KEY` | (required) | OpenRouter key |
| `MODEL_REFRESH_TTL` | 600 | free-list cache TTL (seconds) |
| `MAX_ATTEMPTS` | 8 | max number of models to try as fallbacks |
| `COOLDOWN_SECONDS` | 60 | how long to skip a model after a 429 (seconds) |

## Tests

```bash
pytest
```

## License

MIT — see [LICENSE](LICENSE).
