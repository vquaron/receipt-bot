from __future__ import annotations

import uvicorn

from app.config import load_settings
from app.web.app import create_app


settings = load_settings()
app = create_app(settings)


if __name__ == "__main__":
    uvicorn.run(app, host=settings.web_listen, port=settings.web_port)
