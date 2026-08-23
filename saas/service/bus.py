import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

logger = logging.getLogger("saas.service.bus")


class BaseEventBus(ABC):
    """
    Абстрактный контракт реактивной шины событий.
    Изолирует продюсеров событий от подписчиков (UI, Telegram, SSE).
    """

    @abstractmethod
    async def publish(self, channel: str, event_data: dict[str, Any]) -> None:
        """Опубликовать событие в указанный канал."""
        pass

    @abstractmethod
    def subscribe(self, channel: str) -> asyncio.Queue[dict[str, Any]]:
        """Подписаться на канал и получить очередь входящих событий."""
        pass

    @abstractmethod
    def unsubscribe(self, channel: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Отписаться от канала."""
        pass


class InMemoryEventBus(BaseEventBus):
    """
    Легковесная шина событий на чистом Python (asyncio.Queue).
    Обеспечивает 0.001 мс задержку в рамках одного процесса без внешних брокеров.
    """

    def __init__(self) -> None:
        self._channels: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}

    async def publish(self, channel: str, event_data: dict[str, Any]) -> None:
        queues = self._channels.get(channel)
        if not queues:
            return

        for q in list(queues):
            try:
                q.put_nowait(event_data)
            except asyncio.QueueFull:
                logger.warning(f"[InMemoryEventBus] Queue full for channel {channel}")
            except Exception as e:
                logger.debug(f"[InMemoryEventBus] Publish error on channel {channel}: {e}")

    def subscribe(self, channel: str) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self._channels.setdefault(channel, set()).add(q)
        return q

    def unsubscribe(self, channel: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        if channel in self._channels:
            self._channels[channel].discard(queue)
            if not self._channels[channel]:
                del self._channels[channel]
