import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

import httpx

from core.runner import Core
from core.tasks.parser import DefaultParser

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
    allowed_sectors: list[str] | None = None
    cookies: dict[str, str] = field(default_factory=dict)
    csrf_token: str | None = None
    created_at: float = field(default_factory=time.time)


class BotSession:
    def __init__(self, config: BotConfig):
        self.config = config
        self.status = BotStatus.IDLE
        self.booked = 0
        self.time_live: str | None = None
        self.booked_items: list[dict[str, Any]] = []
        self.error_message: str | None = None
        self.task: asyncio.Task[Any] | None = None
        self.subscribers: set[asyncio.Queue[str]] = set()

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

    async def start(self) -> None:
        if self.is_running():
            self.stop()

        self.status = BotStatus.RUNNING
        self.booked = 0
        self.error_message = None

        parser = DefaultParser(
            allowed_price_ids=self.config.allowed_price_ids,
            allowed_sectors=self.config.allowed_sectors,
        )

        async def emit_callback(event_data: dict[str, Any]):
            if event_data.get("type") == "ticket_booked":
                self.booked = event_data.get("booked", self.booked)
            self.broadcast(event_data)

        core = Core(
            event_id=self.config.event_id,
            target_tickets=self.config.target_tickets,
            num_consumers=self.config.num_consumers,
            parser=parser,
            event_callback=emit_callback,
        )

        initial_headers = None
        if self.config.csrf_token:
            initial_headers = core.fetcher.headers_template.copy()
            initial_headers["X-CSRF-Token"] = self.config.csrf_token

        async def runner():
            try:
                booked = await core.run(
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
            "time_live": self.time_live,
            "error": self.error_message,
        }


class BotManager:
    def __init__(self):
        self.sessions: dict[str, BotSession] = {}

    def get_or_create(self, config: BotConfig) -> BotSession:
        if config.event_id in self.sessions:
            session = self.sessions[config.event_id]
            session.config = config
            return session
        session = BotSession(config)
        self.sessions[config.event_id] = session
        return session

    def get(self, event_id: str) -> BotSession | None:
        return self.sessions.get(event_id)

    def stop(self, event_id: str) -> bool:
        session = self.sessions.get(event_id)
        if session:
            session.stop()
            return True
        return False

    def list_all(self) -> dict[str, Any]:
        tasks = [s.to_dict() for s in self.sessions.values()]
        total_booked = sum(s.booked for s in self.sessions.values())
        return {
            "total_booked": total_booked,
            "tasks": tasks,
        }


# Global singleton instance of BotManager
bot_manager = BotManager()
