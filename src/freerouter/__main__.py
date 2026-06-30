"""`freerouter` / `python -m freerouter` 진입점 — uvicorn으로 프록시 서버 기동."""

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
