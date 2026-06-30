"""Entry point for `freerouter` / `python -m freerouter` — start the proxy via uvicorn."""

from __future__ import annotations

import uvicorn

from .config import settings


def main() -> None:
    uvicorn.run(
        "freerouter.proxy:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
