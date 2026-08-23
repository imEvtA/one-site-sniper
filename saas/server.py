import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from saas.gateway.orchestrator import SaaSGatewayOrchestrator
from saas.gateway.routes import router as saas_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("saas.server")

DB_URL = os.getenv("SAAS_DB_URL", "sqlite+aiosqlite:///sniper.db")
SAAS_HOST = os.getenv("SAAS_HOST", "0.0.0.0")
SAAS_PORT = int(os.getenv("SAAS_PORT", "8001"))

gateway_orchestrator = SaaSGatewayOrchestrator(db_url=DB_URL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting Dedicated SaaS Backend on {SAAS_HOST}:{SAAS_PORT}...")
    app.state.gateway_orchestrator = gateway_orchestrator
    await gateway_orchestrator.start()

    yield

    logger.info("Stopping Dedicated SaaS Backend...")
    await gateway_orchestrator.shutdown()
    logger.info("SaaS Backend shutdown complete.")


app = FastAPI(
    title="Ticketpro SaaS Core & Gateway Service",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS для вызовов из Web HUD / Proxy / Telegram
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.state.gateway_orchestrator = gateway_orchestrator
app.include_router(saas_router)


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "saas-core"}


def main():
    uvicorn.run(
        "saas.server:app",
        host=SAAS_HOST,
        port=SAAS_PORT,
        reload=False,
        timeout_graceful_shutdown=1,
    )


if __name__ == "__main__":
    main()
