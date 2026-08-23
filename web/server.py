import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import uvicorn

from web.proxy import router as proxy_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("web.server")

STATIC_DIR = Path(__file__).parent / "static"
WEB_PORT = int(os.getenv("WEB_PORT", "8000"))
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")

app = FastAPI(
    title="Ticketpro Web Proxy & HUD",
    version="1.0.0",
)

# Static assets (HUD overlay script & styles)
app.mount("/proxy-static", StaticFiles(directory=STATIC_DIR), name="proxy-static")

# Reverse Proxy routes (proxies Ticketpro and SaaS Gateway endpoints)
app.include_router(proxy_router)


def main():
    logger.info(f"Starting Ticketpro Web Proxy on {WEB_HOST}:{WEB_PORT}...")
    uvicorn.run(
        "web.server:app",
        host=WEB_HOST,
        port=WEB_PORT,
        reload=False,
        timeout_graceful_shutdown=1,
    )


if __name__ == "__main__":
    main()
