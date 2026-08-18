import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from web.api import router as bot_router
from web.proxy import router as proxy_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("web.server")

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Ticketpro Reverse Proxy & Fast Sniper")

# Mount overlay static assets
app.mount("/proxy-static", StaticFiles(directory=STATIC_DIR), name="proxy-static")

# Include Bot Control API routes
app.include_router(bot_router)

# Include Reverse Proxy and direct checkout routes (must be included last)
app.include_router(proxy_router)


def main():
    import uvicorn
    uvicorn.run("web.server:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
