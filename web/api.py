import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from core.bot import BotConfig, bot_manager

logger = logging.getLogger("web.api")

router = APIRouter(prefix="/api/bot", tags=["bot"])


class StartBotRequest(BaseModel):
    event_id: str
    event_name: str | None = None
    target_tickets: int = 1
    num_consumers: int = 5
    poll_interval: float = 1.0
    allowed_price_ids: list[str] | None = None
    allowed_sectors: list[str] | None = None
    csrf_token: str | None = None


class StopBotRequest(BaseModel):
    event_id: str


class ActivateSessionRequest(BaseModel):
    event_id: str


@router.post("/start")
async def start_bot(request: Request, req: StartBotRequest):
    browser_cookies = dict(request.cookies)
    csrf_token = req.csrf_token or request.headers.get("x-csrf-token")
    event_name = req.event_name or f"Событие #{req.event_id}"

    logger.info(f"[API START] Event: {req.event_id} ({event_name}) | Cookies: {browser_cookies}")

    config = BotConfig(
        event_id=req.event_id,
        event_name=event_name,
        target_tickets=req.target_tickets,
        num_consumers=req.num_consumers,
        poll_interval=req.poll_interval,
        allowed_price_ids=req.allowed_price_ids,
        allowed_sectors=req.allowed_sectors,
        cookies=browser_cookies.copy(),
        csrf_token=csrf_token,
    )

    session = bot_manager.get_or_create(config)
    await session.start()

    return {"status": "ok", "message": f"Sniper started for {event_name}", "event_id": req.event_id}


@router.post("/stop")
async def stop_bot(req: StopBotRequest):
    stopped = bot_manager.stop(req.event_id)
    if stopped:
        return {"status": "ok", "message": f"Sniper stopped for event {req.event_id}"}
    return {"status": "not_found", "message": f"No active session for event {req.event_id}"}


@router.get("/tasks")
async def get_all_tasks():
    return bot_manager.list_all()


@router.get("/status")
async def get_bot_status(event_id: str = Query(...)):
    session = bot_manager.get(event_id)
    if session:
        return session.to_dict()
    return {"status": "idle", "target": 0, "booked": 0, "event_name": f"Событие #{event_id}"}


@router.post("/activate-session")
async def activate_session(req: ActivateSessionRequest):
    session = bot_manager.get(req.event_id)
    if session and session.config.cookies:
        response = JSONResponse({"status": "ok", "message": f"Session switched to event {req.event_id}"})
        for k, v in session.config.cookies.items():
            response.set_cookie(key=k, value=v, path="/", httponly=True, samesite="lax")
        return response
    return JSONResponse({"status": "error", "message": "Session not found"}, status_code=404)


@router.get("/stream")
async def bot_stream(event_id: str = Query(...)):
    session = bot_manager.get(event_id)
    if not session:
        config = BotConfig(event_id=event_id, event_name=f"Событие #{event_id}")
        session = bot_manager.get_or_create(config)

    async def event_generator():
        q = session.subscribe()
        initial_msg = {
            "type": "status",
            "event_id": event_id,
            "event_name": session.config.event_name,
            "message": f"Connected (status: {session.get_status_str()}, booked: {session.booked}/{session.config.target_tickets})",
        }
        yield f"data: {json.dumps(initial_msg)}\n\n"

        try:
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=20.0)
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                except asyncio.CancelledError:
                    break
        finally:
            session.unsubscribe(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
