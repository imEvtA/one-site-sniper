import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Type

import httpx

from core.runner import Core
from core.tasks.fetcher import Fetcher
from core.tasks.parser import BaseParser, DefaultParser


logger = logging.getLogger("core.bot")


class BotStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    FINISHED = "finished"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class BotConfig:
    event_id: str
    event_name: str
    target_tickets: int = 1
    num_consumers: int = 5
    poll_interval: float = 1.0
    allowed_price_ids: list[str] | None = None
    min_price: float | None = None
    max_price: float | None = None
    allowed_sectors: list[str] | None = None
    parser_class: type[BaseParser] = DefaultParser
    valid_price_ids: set[str] = field(default_factory=set)
    event_prices: dict[str, dict[str, Any]] = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)
    csrf_token: str | None = None
    created_at: float = field(default_factory=time.time)

    def __post_init__(self):
        if self.allowed_price_ids:
            self.valid_price_ids = set(self.allowed_price_ids)

    def update_filters(
        self,
        allowed_price_ids: list[str] | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        allowed_sectors: list[str] | None = None,
        target_tickets: int | None = None,
        num_consumers: int | None = None,
    ) -> None:
        """
        Обновляет параметры фильтрации и пересчитывает множество валидных цен.
        """
        if allowed_price_ids is not None:
            self.allowed_price_ids = allowed_price_ids
        if min_price is not None:
            self.min_price = min_price
        if max_price is not None:
            self.max_price = max_price
        if allowed_sectors is not None:
            self.allowed_sectors = allowed_sectors
        if target_tickets is not None:
            self.target_tickets = target_tickets
        if num_consumers is not None:
            self.num_consumers = num_consumers

        if self.event_prices:
            self.update_valid_prices(self.event_prices)

    def update_valid_prices(self, prices: dict[str, dict[str, Any]] | dict[str, Any]) -> set[str]:
        """
        Заполняет event_prices и рассчитывает множество valid_price_ids
        согласно текущим фильтрам (allowed_price_ids, min_price, max_price).
        """
        self.event_prices = prices
        matched_ids: set[str] = set()

        for pid, pdata in prices.items():
            pid_str = str(pid)
            price_val = float(pdata.get("price", 0)) if isinstance(pdata, dict) else float(pdata)

            if self.allowed_price_ids and pid_str not in self.allowed_price_ids:
                continue
            if self.min_price is not None and price_val < self.min_price:
                continue
            if self.max_price is not None and price_val > self.max_price:
                continue

            matched_ids.add(pid_str)

        has_filters = (
            (self.allowed_price_ids is not None and len(self.allowed_price_ids) > 0)
            or self.min_price is not None
            or self.max_price is not None
        )

        self.valid_price_ids.clear()
        if has_filters:
            self.valid_price_ids.update(matched_ids)
        else:
            self.valid_price_ids.update(str(pid) for pid in prices.keys())

        logger.info(
            f"[BotConfig] Event {self.event_id} prices parsed: {len(prices)}. "
            f"Valid price_ids ({len(self.valid_price_ids)}): {self.valid_price_ids}"
        )
        return self.valid_price_ids


class BotSession:
    """
    Фасад конкретного снайпера. Полностью инкапсулирует парсер, фетчер и runner (Core).
    Весь внешний мир (Web API, Telegram, UI) взаимодействует со снайпером только через BotSession.
    """
    def __init__(self, config: BotConfig):
        self.config = config
        self.status = BotStatus.IDLE
        self.booked = 0
        self.time_live: str | None = None
        self.booked_items: list[dict[str, Any]] = []
        self.error_message: str | None = None
        self.task: asyncio.Task[Any] | None = None
        self.subscribers: set[asyncio.Queue[str]] = set()

        # Инкапсулированные компоненты ядра
        self.parser: BaseParser = self.config.parser_class(
            allowed_price_ids=self.config.allowed_price_ids,
            allowed_sectors=self.config.allowed_sectors,
            valid_price_ids=self.config.valid_price_ids,
        )
        self.fetcher: Fetcher = Fetcher(
            event_id=self.config.event_id,
            parser=self.parser,
            config=self.config,
        )
        self.core: Core | None = None

    def is_running(self) -> bool:
        return self.task is not None and not self.task.done()

    def get_status_str(self) -> str:
        if self.is_running():
            return BotStatus.RUNNING.value
        return self.status.value

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue()
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        self.subscribers.discard(q)

    def broadcast(self, event_data: dict[str, Any]) -> None:
        payload = {
            "event_id": self.config.event_id,
            "event_name": self.config.event_name,
            **event_data,
        }
        msg = json.dumps(payload)
        for q in list(self.subscribers):
            try:
                q.put_nowait(msg)
            except Exception:
                pass

    async def get_prices(self, force_refresh: bool = False) -> dict[str, dict[str, Any]]:
        """
        [Слой 1: Инициализация цен]
        Запрашивает и возвращает каталог цен события через инкапсулированный Fetcher.
        """
        if self.config.event_prices and not force_refresh:
            return self.config.event_prices

        prices = await self.fetcher.fetch_prices()
        return prices



    def set_filters(
        self,
        allowed_price_ids: list[str] | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        allowed_sectors: list[str] | None = None,
        target_tickets: int | None = None,
        num_consumers: int | None = None,
    ) -> set[str]:
        """
        [Слой 2: Настройка фильтров]
        Обновляет фильтры в конфигурации и синхронизирует их с парсером.
        """
        self.config.update_filters(
            allowed_price_ids=allowed_price_ids,
            min_price=min_price,
            max_price=max_price,
            allowed_sectors=allowed_sectors,
            target_tickets=target_tickets,
            num_consumers=num_consumers,
        )
        if hasattr(self.parser, "allowed_sectors"):
            self.parser.allowed_sectors = set(allowed_sectors) if allowed_sectors else None
        if hasattr(self.parser, "allowed_price_ids"):
            self.parser.allowed_price_ids = set(allowed_price_ids) if allowed_price_ids else None
        return self.config.valid_price_ids

    async def start(self) -> None:
        """
        [Слой 2: Запуск охоты]
        Запускает цикл снайпинга билетов по установленным фильтрам.
        """
        if self.is_running():
            self.stop()

        self.status = BotStatus.RUNNING
        self.booked = 0
        self.error_message = None

        # Синхронизируем инкапсулированный парсер
        self.parser = self.config.parser_class(
            allowed_price_ids=self.config.allowed_price_ids,
            allowed_sectors=self.config.allowed_sectors,
            valid_price_ids=self.config.valid_price_ids,
        )
        self.fetcher = Fetcher(
            event_id=self.config.event_id,
            parser=self.parser,
            config=self.config,
        )

        async def emit_callback(event_data: dict[str, Any]):
            if event_data.get("type") == "ticket_booked":
                self.booked = event_data.get("booked", self.booked)
            self.broadcast(event_data)

        self.core = Core(
            event_id=self.config.event_id,
            target_tickets=self.config.target_tickets,
            num_consumers=self.config.num_consumers,
            parser=self.parser,
            config=self.config,
            event_callback=emit_callback,
        )

        initial_headers = None
        if self.config.csrf_token:
            initial_headers = self.fetcher.headers_template.copy()
            initial_headers["X-CSRF-Token"] = self.config.csrf_token

        async def runner():
            try:
                booked = await self.core.run(
                    initial_cookies=self.config.cookies if self.config.cookies else None,
                    initial_headers=initial_headers,
                )
                self.booked = booked
                self.status = BotStatus.FINISHED
            except asyncio.CancelledError:
                self.status = BotStatus.STOPPED
                self.broadcast({"type": "status", "message": "Снайпер остановлен пользователем"})
            except Exception as e:
                logger.exception(f"Error in bot runner for {self.config.event_id}: {e}")
                self.status = BotStatus.ERROR
                self.error_message = str(e)
                self.broadcast({"type": "error", "message": str(e)})

        self.task = asyncio.create_task(runner())

    def stop(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
        self.status = BotStatus.STOPPED

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.config.event_id,
            "event_name": self.config.event_name,
            "status": self.get_status_str(),
            "target": self.config.target_tickets,
            "booked": self.booked,
            "min_price": self.config.min_price,
            "max_price": self.config.max_price,
            "allowed_prices_count": len(self.config.valid_price_ids),
            "valid_price_ids": list(self.config.valid_price_ids),
            "event_prices": self.config.event_prices,
            "time_live": self.time_live,
            "error": self.error_message,
        }


class BotManager:
    def __init__(self):
        self.sessions: dict[str, BotSession] = {}

    def get_or_create(
        self,
        event_id: str | None = None,
        config: BotConfig | None = None,
        event_name: str | None = None,
        parser_class: type[BaseParser] | None = None,
    ) -> BotSession:
        eid = config.event_id if config else (event_id or "")
        if eid in self.sessions:
            session = self.sessions[eid]
            if config:
                session.config = config
            return session

        cfg = config or BotConfig(
            event_id=eid,
            event_name=event_name or f"Событие #{eid}",
            parser_class=parser_class or DefaultParser,
        )
        session = BotSession(cfg)
        self.sessions[eid] = session
        return session

    def get(self, event_id: str) -> BotSession | None:
        return self.sessions.get(event_id)

    def remove(self, event_id: str) -> bool:
        session = self.sessions.pop(event_id, None)
        if session:
            session.stop()
            return True
        return False

    def stop(self, event_id: str) -> bool:

        session = self.sessions.get(event_id)
        if session:
            session.stop()
            return True
        return False

    def stop_all(self) -> int:
        count = 0
        for session in self.sessions.values():
            if session.is_running():
                session.stop()
                count += 1
        logger.info(f"[BotManager] Stopped {count} active bot sessions on shutdown.")
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



# Global singleton instance of BotManager
bot_manager = BotManager()
