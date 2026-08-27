import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx

from core.pipeline import (
    HuntingContext,
    PipelineContext,
    PreflightError,
    PreflightPipeline,
    build_default_preflight_pipeline,
    build_presession_pipeline,
    build_start_pipeline,
)
from core.tasks.consumer import AtomicCounter, ConsumerPool
from core.tasks.fetcher import Fetcher
from core.tasks.parser import BaseParser, Parser, Ticket

logger = logging.getLogger("core.bot")


class BotStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    FINISHED = "finished"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class PresessionData:
    """
    Легковесный контекст предсессии для хранения метаданных мероприятия до старта.
    Содержит предварительно разрезолвленный SVG URL схемы зала для мгновенного старта.
    """
    event_id: str
    event_name: str
    prices: list[dict[str, Any]] = field(default_factory=list)
    valid_price_ids: list[str] = field(default_factory=list)
    cookies: dict[str, str] = field(default_factory=dict)
    csrf_token: str | None = None
    svg_url: str | None = None
    page_status: int = 200
    error: dict[str, str] | None = None
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "ok" if not self.error else "error",
            "event_id": self.event_id,
            "event_name": self.event_name,
            "page_status": self.page_status,
            "prices": self.prices,
            "valid_price_ids": self.valid_price_ids,
            "has_csrf": bool(self.csrf_token),
            "svg_url": self.svg_url,
            "has_scheme": bool(self.svg_url),
            "error": self.error,
        }


class BotSession:
    """
    Автономный бот-снайпер для конкретного мероприятия.
    Получает готовый HuntingContext после успешного прохождения Preflight Pipeline
    и занимается исключительно скоростным снайпингом билетов.
    """
    def __init__(self, ctx: HuntingContext, parser_class: type[BaseParser] = Parser):
        self.ctx = ctx
        self.status = BotStatus.IDLE
        self.booked = 0
        self.time_live: str | None = None
        self.booked_items: list[dict[str, Any]] = []
        self.error_message: str | None = None
        self.task: asyncio.Task[Any] | None = None
        self.subscribers: set[asyncio.Queue[str | None]] = set()

        self.parser: BaseParser = parser_class(
            allowed_sectors=self.ctx.allowed_sectors,
            valid_price_ids=self.ctx.valid_price_ids,
        )
        self.fetcher: Fetcher = Fetcher(event_id=self.ctx.event_id)
        self.consumer_pool: ConsumerPool | None = None

    @property
    def event_id(self) -> str:
        return self.ctx.event_id

    @property
    def event_name(self) -> str:
        return self.ctx.event_name

    def is_running(self) -> bool:
        return self.task is not None and not self.task.done()

    def get_status_str(self) -> str:
        if self.is_running():
            return BotStatus.RUNNING.value
        return self.status.value

    def subscribe(self) -> asyncio.Queue[str | None]:
        q: asyncio.Queue[str | None] = asyncio.Queue()
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str | None]) -> None:
        self.subscribers.discard(q)

    def broadcast(self, event_data: dict[str, Any]) -> None:
        payload = {
            "event_id": self.ctx.event_id,
            "event_name": self.ctx.event_name,
            **event_data,
        }
        msg = json.dumps(payload)
        for q in list(self.subscribers):
            try:
                q.put_nowait(msg)
            except Exception:
                pass

    async def start(self) -> None:
        """
        Запускает фоновую задачу охоты снайпера.
        """
        if self.is_running():
            logger.info(f"[BotSession] Event {self.ctx.event_id} is already running.")
            return

        self.status = BotStatus.RUNNING
        self.error_message = None
        self.task = asyncio.create_task(self._hunt())
        logger.info(f"[BotSession] Started sniper task for event {self.ctx.event_id}")

    def stop(self) -> None:
        """
        Останавливает снайпер, отменяет фоновые задачи и закрывает подписчиков.
        """
        if self.consumer_pool:
            self.consumer_pool.is_running = False
        if self.task and not self.task.done():
            self.task.cancel()
        self.status = BotStatus.STOPPED
        self.broadcast({"type": "status", "status": "stopped", "message": "Снайпер остановлен"})
        for q in list(self.subscribers):
            try:
                q.put_nowait(None)
            except Exception:
                pass
        logger.info(f"[BotSession] Stopped sniper for event {self.ctx.event_id}")

    async def _run_producer_loop(
        self,
        svg_url: str,
        queue: asyncio.Queue[tuple[str, str] | None],
        counter: AtomicCounter,
        client: httpx.AsyncClient,
    ) -> None:
        """
        Цикл быстрого опроса SVG-схемы зала.
        """
        attempted_tickets: set[str] = set()
        iteration = 0
        target = self.ctx.target_tickets

        while not await counter.is_completed():
            iteration += 1
            logger.info(f"[BotSession] Iteration #{iteration} (Booked: {counter.value}/{target})...")
            self.broadcast({
                "type": "status",
                "message": f"Охота (итерация #{iteration}, поймано: {counter.value}/{target})...",
                "booked": counter.value,
                "target": target,
            })

            svg_text = await self.fetcher.fetch_svg(svg_url, client=client)
            if svg_text:
                tickets = self.parser.parse(svg_text)
                new_count = 0
                for ticket in tickets:
                    if ticket.ticket_id not in attempted_tickets:
                        attempted_tickets.add(ticket.ticket_id)
                        await queue.put((ticket.ticket_id, ticket.price_id))
                        new_count += 1

                if new_count > 0:
                    self.broadcast({
                        "type": "tickets_streamed",
                        "found_count": new_count,
                        "booked": counter.value,
                        "target": target,
                    })

            if await counter.is_completed():
                break

            await asyncio.sleep(self.ctx.poll_interval)

    async def _hunt(
        self,
        get_client: httpx.AsyncClient | None = None,
        post_client: httpx.AsyncClient | None = None,
    ) -> int:
        """
        Главный оркестратор охоты.
        """
        eid = self.ctx.event_id
        target = self.ctx.target_tickets
        svg_url = self.ctx.svg_url

        logger.info(
            f"[BotSession] 🎯 Launching hunt for {eid} ({self.ctx.event_name}) | "
            f"Target: {target} | Consumers: {self.ctx.num_consumers} | Valid prices: {self.ctx.valid_price_ids}"
        )
        self.broadcast({"type": "status", "status": "running", "message": "Старт охоты за билетами..."})
        self.broadcast({"type": "session_initialized", "svg_url": svg_url})

        manage_get = get_client is None
        manage_post = post_client is None
        active_get_client = get_client or httpx.AsyncClient(base_url=self.fetcher.event_url)

        headers = self.fetcher.headers_template.copy()
        if self.ctx.csrf_token:
            headers["X-CSRF-Token"] = self.ctx.csrf_token

        active_post_client = post_client or httpx.AsyncClient(cookies=self.ctx.cookies, headers=headers)

        queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()
        counter = AtomicCounter(target=target)

        async def handle_ticket_booked(ticket_id: str, price_id: str, raw_resp: dict):
            self.booked = counter.value
            self.booked_items.append({"ticket_id": ticket_id, "price_id": price_id, "time": time.time()})
            logger.info(f"[BotSession] 🎉 Booked ticket {ticket_id} (price: {price_id})! Progress: {self.booked}/{target}")
            self.broadcast({
                "type": "ticket_booked",
                "ticket_id": ticket_id,
                "price_id": price_id,
                "booked": self.booked,
                "target": target,
            })

        try:
            # 1. Запуск пула консьюмеров
            self.consumer_pool = ConsumerPool(
                num_consumers=self.ctx.num_consumers,
                queue=queue,
                counter=counter,
                cookies=self.ctx.cookies,
                headers=headers,
                on_book_callback=handle_ticket_booked,
            )
            self.consumer_pool.start(client=active_post_client)

            # 2. Цикл поиска
            try:
                await self._run_producer_loop(svg_url, queue, counter, active_get_client)
                await self.consumer_pool.shutdown()

                self.status = BotStatus.FINISHED
                self.booked = counter.value
                logger.info(f"[BotSession] 🏁 Hunt completed for {eid}. Booked: {self.booked}/{target}")
                self.broadcast({"type": "finished", "booked": self.booked, "target": target})
                return self.booked

            except asyncio.CancelledError:
                logger.info(f"[BotSession] Hunt cancelled for {eid}")
                if self.consumer_pool:
                    await self.consumer_pool.shutdown()
                self.status = BotStatus.STOPPED
                raise
            except Exception as e:
                logger.exception(f"[BotSession] Error during hunting: {e}")
                if self.consumer_pool:
                    await self.consumer_pool.shutdown()
                self.status = BotStatus.ERROR
                self.error_message = str(e)
                self.broadcast({"type": "error", "message": str(e)})
                return counter.value
        finally:
            if manage_post:
                await active_post_client.aclose()
            if manage_get:
                await active_get_client.aclose()

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.ctx.event_id,
            "event_name": self.ctx.event_name,
            "status": self.get_status_str(),
            "target": self.ctx.target_tickets,
            "booked": self.booked,
            "num_consumers": self.ctx.num_consumers,
            "valid_price_ids": list(self.ctx.valid_price_ids),
            "prices_count": len(self.ctx.all_event_prices),
            "time_live": self.time_live,
            "error_message": self.error_message,
        }


class BotManager:
    """
    Фасад и реестр управления жизненным циклом ботов и предсессий.
    """
    def __init__(self, parser_class: type[BaseParser] = Parser):
        self.presessions: dict[str, PresessionData] = {}
        self.sessions: dict[str, BotSession] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.parser: BaseParser = parser_class()
        self.presession_pipeline = build_presession_pipeline()
        self.start_pipeline = build_start_pipeline()
        self.default_pipeline = build_default_preflight_pipeline()

    def _get_lock(self, event_id: str) -> asyncio.Lock:
        if event_id not in self._locks:
            self._locks[event_id] = asyncio.Lock()
        return self._locks[event_id]

    async def prepare_presession(
        self,
        event_id: str,
        html_text: str,
        cookies: dict[str, str],
        event_name: str | None = None,
        page_status: int = 200,
    ) -> PresessionData:
        """
        [Фаза 1: Presession Pipeline]
        Парсит метаданные мероприятия из HTML страницы, запускает пресессионный конвейер
        (статус, защита, токен, резолвинг SVG схемы) и сохраняет готовую предсессию.
        """
        eid = str(event_id)
        name = event_name or f"Событие #{eid}"

        if page_status == 200:
            prices_dict = self.parser.extract_event_prices(html_text)
            prices_list = list(prices_dict.values()) if prices_dict else []
            valid_ids = [str(k) for k in prices_dict.keys()] if prices_dict else []

            import re
            csrf_token = None
            token_match = re.search(r'name="csrf-token" content="(.*?)"', html_text)
            if token_match:
                csrf_token = token_match.group(1)
        else:
            prices_list = []
            valid_ids = []
            csrf_token = None

        ctx = PipelineContext(
            event_id=eid,
            event_name=name,
            raw_cookies=cookies.copy(),
            csrf_token=csrf_token,
            page_status=page_status,
            all_event_prices=prices_list,
        )

        error_data = None
        try:
            # Выполняем пассивный конвейер предсессии (проверка статуса, авторизации и получение схемы)
            for step in self.presession_pipeline.steps:
                await step.execute(ctx)
        except PreflightError as pe:
            logger.warning(f"[BotManager] Presession preflight error for event {eid}: {pe}")
            error_data = pe.to_dict()
        except Exception as e:
            logger.warning(f"[BotManager] Unexpected presession error for event {eid}: {e}")
            error_data = {
                "code": "PRESESSION_ERROR",
                "message": str(e),
                "hint": "Обновите страницу мероприятия (F5).",
                "step": "PresessionPipeline",
            }

        presession = PresessionData(
            event_id=eid,
            event_name=name,
            prices=prices_list,
            valid_price_ids=valid_ids,
            cookies=ctx.raw_cookies,
            csrf_token=ctx.csrf_token,
            svg_url=ctx.svg_url,
            page_status=page_status,
            error=error_data,
            updated_at=time.time(),
        )
        self.presessions[eid] = presession
        logger.info(
            f"[BotManager] Presession prepared for event {eid} "
            f"(status: {page_status}, prices: {len(prices_list)}, svg: {bool(ctx.svg_url)}, err: {bool(error_data)})"
        )
        return presession

    def get_presession(self, event_id: str) -> PresessionData | None:
        return self.presessions.get(str(event_id))

    async def start_session(
        self,
        req: Any,
        cookies: dict[str, str],
        pipeline: PreflightPipeline | None = None,
    ) -> BotSession:
        """
        [Фаза 2: Start Pipeline]
        Мгновенный старт снайпера: берет предварительно разрезолвленную схему из предсессии,
        валидирует фильтры цен пользователя и сразу запускает охоту без лишних сетевых задержек.
        """
        eid = str(req.event_id)

        async with self._get_lock(eid):
            # Остановка предыдущей сессии для этого же мероприятия, если она запущена
            existing = self.sessions.get(eid)
            if existing and existing.is_running():
                existing.stop()
                # Ждем короткий тик, чтобы фоновый таск успел завершиться
                await asyncio.sleep(0.01)

            # Извлекаем предсессию
            presession = self.presessions.get(eid)
            all_prices = presession.prices if presession else []
            fallback_csrf = presession.csrf_token if presession else None
            cached_svg_url = presession.svg_url if presession else None

            merged_cookies = (presession.cookies.copy() if presession else {})
            merged_cookies.update(cookies)

            # Конвертируем запрос в PipelineContext с уже известным svg_url
            if hasattr(req, "to_pipeline_context"):
                ctx = req.to_pipeline_context(cookies=merged_cookies, all_event_prices=all_prices, svg_url=cached_svg_url)
            else:
                ctx = PipelineContext(
                    event_id=eid,
                    event_name=getattr(req, "event_name", None) or f"Событие #{eid}",
                    target_tickets=getattr(req, "target_tickets", 1),
                    num_consumers=getattr(req, "num_consumers", 5),
                    poll_interval=getattr(req, "poll_interval", 1.0),
                    raw_cookies=merged_cookies,
                    csrf_token=getattr(req, "csrf_token", None) or fallback_csrf,
                    page_status=getattr(req, "page_status", 200) or 200,
                    allowed_price_ids=getattr(req, "allowed_price_ids", None),
                    min_price=getattr(req, "min_price", None),
                    max_price=getattr(req, "max_price", None),
                    allowed_sectors=getattr(req, "allowed_sectors", None),
                    all_event_prices=all_prices,
                    svg_url=cached_svg_url,
                )

            if not ctx.csrf_token and fallback_csrf:
                ctx.csrf_token = fallback_csrf

            # Если пользователь передал свой пайплайн — используем его
            if pipeline:
                runner = pipeline
            elif ctx.svg_url:
                # Схема уже известна из предсессии -> быстрый старт за 0мс!
                runner = self.start_pipeline
            else:
                # Схема ещё не разрезолвлена (прямой API вызов) -> полный конвейер
                runner = self.default_pipeline

            hunting_ctx = await runner.run(ctx)

            # Создание и немедленный запуск сессии
            session = BotSession(hunting_ctx)
            self.sessions[eid] = session
            await session.start()
            return session


    def get(self, event_id: str) -> BotSession | None:
        return self.sessions.get(str(event_id))

    def stop(self, event_id: str) -> bool:
        session = self.sessions.get(str(event_id))
        if session:
            session.stop()
            return True
        return False

    async def stop_all(self) -> int:
        logger.info(f"[BotManager] trying to stop {len(self.sessions.values())} active bot sessions...")
        tasks = []
        count = 0
        for session in list(self.sessions.values()):
            if session.is_running():
                if session.task and not session.task.done():
                    tasks.append(session.task)
                session.stop()
                count += 1
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info(f"[BotManager] Cleanly stopped {count} active bot sessions on shutdown.")
        return count

    def list_all(self) -> dict[str, Any]:
        tasks = [
            s.to_dict()
            for s in self.sessions.values()
            if s.status != BotStatus.IDLE or s.is_running()
        ]
        total_booked = sum(s.booked for s in self.sessions.values())
        return {
            "total_booked": total_booked,
            "active_count": sum(1 for s in self.sessions.values() if s.is_running()),
            "tasks": tasks,
        }


# Глобальный синглтон менеджера ботов
bot_manager = BotManager()
