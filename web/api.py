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
    min_price: float | None = None
    max_price: float | None = None
    allowed_sectors: list[str] | None = None
    csrf_token: str | None = None
    page_status: int | None = 200


class StopBotRequest(BaseModel):
    event_id: str


class ActivateSessionRequest(BaseModel):
    event_id: str


@router.get("/event-prices")
async def get_event_prices(event_id: str = Query(...)):
    """
    [Слой 1: Инициализация и каталог цен]
    Запрашивает доступные категории цен без создания сессии в реестре задач.
    """
    session = bot_manager.get(event_id)
    if session and session.config.event_prices:
        return {
            "status": "ok",
            "event_id": event_id,
            "prices": session.config.event_prices,
            "valid_price_ids": list(session.config.valid_price_ids),
        }

    # Если сессии нет, запрашиваем цены через Fetcher без засорения реестра
    fetcher = Fetcher(event_id=event_id)
    prices = await fetcher.fetch_prices()
    if prices:
        if session:
            session.config.event_prices = prices
            session.config.update_valid_prices(prices)
        return {
            "status": "ok",
            "event_id": event_id,
            "prices": prices,
            "valid_price_ids": list(prices.keys()),
        }

    return JSONResponse(
        {"status": "error", "message": f"Мероприятие #{event_id} недоступно или не найдено"},
        status_code=404
    )


@router.post("/start")
async def start_bot(request: Request, req: StartBotRequest):
    """
    [Слой 2: Валидация статуса страницы, настройка фильтров и запуск охоты]
    Создает и регистрирует сессию только после успешной валидации.
    """
    # 1. Валидация статуса страницы (мгновенный отказ при не-200)
    if req.page_status is not None and not (200 <= req.page_status < 300):
        logger.warning(f"[API START REJECTED] Event {req.event_id} rejected due to page status {req.page_status}")
        return JSONResponse(
            {"status": "error", "message": f"Отказ инициализации: статус страницы {req.page_status}"},
            status_code=400
        )

    browser_cookies = dict(request.cookies)
    csrf_token = req.csrf_token or request.headers.get("x-csrf-token")
    event_name = req.event_name or f"Событие #{req.event_id}"

    logger.info(
        f"[API START] Event: {req.event_id} ({event_name}) | "
        f"Prices filter: ids={req.allowed_price_ids}, min={req.min_price}, max={req.max_price} | "
        f"Cookies: {browser_cookies}"
    )

    # Создаем/получаем сессию только после прохождения валидации
    session = bot_manager.get_or_create(event_id=req.event_id, event_name=event_name)
    session.config.cookies = browser_cookies.copy()
    if csrf_token:
        session.config.csrf_token = csrf_token

    # 2. Применяем фильтры цен, билетов и секторов
    session.set_filters(
        allowed_price_ids=req.allowed_price_ids,
        min_price=req.min_price,
        max_price=req.max_price,
        allowed_sectors=req.allowed_sectors,
        target_tickets=req.target_tickets,
        num_consumers=req.num_consumers,
    )

    # 3. Запускаем охоту
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
    """
    SSE stream для live-логов и событий охоты.
    Подключается только к существующей сессии бота.
    """
    session = bot_manager.get(event_id)
    if not session:
        return JSONResponse({"status": "error", "message": "Session not found"}, status_code=404)

    async def event_generator():
        q = session.subscribe()
        try:
            yield f"data: {json.dumps({'type': 'init', **session.to_dict()})}\n\n"
            while True:
                data = await q.get()
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
