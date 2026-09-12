from __future__ import annotations

import uvicorn

from app.core.config import load_settings


def main() -> None:
    settings = load_settings()
    uvicorn.run(
        "app.api.application:app",
        host=settings.host,
        port=settings.port,
    )


if __name__ == "__main__":
    main()
