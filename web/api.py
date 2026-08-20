import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from core.bot import BotStatus, bot_manager
from core.pipeline.exceptions import PreflightError
from core.schemas import (
    ActivateSessionRequest,
    PresessionResponse,
    StartBotRequest,
    StopBotRequest,
)

logger = logging.getLogger("web.api")

router = APIRouter(prefix="/api/bot", tags=["bot"])


@router.get("/presession")
async def get_presession(event_id: str = Query(...)):
    """
    Возвращает метаданные предсессии (список категорий цен, CSRF статус) для отрисовки HUD.
    """
    presession = bot_manager.get_presession(event_id)
    if presession:
        return presession.to_dict()

    return JSONResponse(
        {"status": "error", "message": f"Предсессия для мероприятия #{event_id} не найдена"},
        status_code=404,
    )


@router.get("/event-prices")
async def get_event_prices(event_id: str = Query(...)):
    """
    Эндпоинт обратной совместимости для получения списка цен.
    """
    presession = bot_manager.get_presession(event_id)
    if presession and presession.prices:
        return {
            "status": "ok",
            "event_id": event_id,
            "prices": {str(p.get("id", "")): p for p in presession.prices},
            "valid_price_ids": presession.valid_price_ids,
        }

    return JSONResponse(
        {"status": "error", "message": f"Мероприятие #{event_id} недоступно или не инициализировано"},
        status_code=404,
    )


@router.post("/start")
async def start_bot(request: Request, req: StartBotRequest):
    """
    Запускает снайпер через Preflight Pipeline и BotManager.
    При сбое проверок возвращает типизированную ошибку с инструкцией по решению.
    """
    browser_cookies = dict(request.cookies)
    try:
        session = await bot_manager.start_session(req=req, cookies=browser_cookies)
        return {
            "status": "ok",
            "message": f"Sniper started for {session.event_name}",
            "event_id": session.event_id,
        }
    except PreflightError as err:
        logger.warning(f"[API START PREFLIGHT FAILED] {err}")
        return JSONResponse(
            {
                "status": "error",
                "message": err.message,
                "error": err.to_dict(),
            },
            status_code=400,
        )
    except Exception as exc:
        logger.exception(f"[API START UNEXPECTED ERROR] {exc}")
        return JSONResponse(
            {
                "status": "error",
                "message": f"Внутренняя ошибка запуска снайпера: {exc}",
            },
            status_code=500,
        )


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
    cookies = session.ctx.cookies if session else None
    if not cookies:
        presession = bot_manager.get_presession(req.event_id)
        cookies = presession.cookies if presession else None

    if cookies:
        response = JSONResponse({"status": "ok", "message": f"Session switched to event {req.event_id}"})
        for k, v in cookies.items():
            response.set_cookie(key=k, value=v, path="/", httponly=True, samesite="lax")
        return response

    return JSONResponse({"status": "error", "message": "Session not found"}, status_code=404)


@router.get("/stream")
async def bot_stream(request: Request, event_id: str = Query(...)):
    """
    SSE stream для live-логов и событий охоты.
    """
    session = bot_manager.get(event_id)
    if not session:
        return JSONResponse({"status": "error", "message": "Session not found"}, status_code=404)

    async def event_generator():
        q = session.subscribe()
        try:
            yield f"data: {json.dumps({'type': 'init', **session.to_dict()})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(q.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    if session.status in (BotStatus.STOPPED, BotStatus.FINISHED, BotStatus.ERROR) or not session.is_running():
                        break
                    yield ": ping\n\n"
                    continue

                if data is None:
                    break
                yield f"data: {data}\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            session.unsubscribe(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
