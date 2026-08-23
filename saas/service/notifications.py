import asyncio
import json
import logging
from typing import Any, AsyncIterator

from saas.service.bus import BaseEventBus

logger = logging.getLogger("saas.service.notifications")


class NotificationManager:
    """
    Менеджер уведомлений.
    Адаптирует события из шины BaseEventBus в потоки данных для интерфейсов (SSE, Telegram).
    """

    def __init__(self, event_bus: BaseEventBus) -> None:
        self.event_bus = event_bus

    async def stream_user_events(self, user_id: str) -> AsyncIterator[str]:
        """
        Асинхронный генератор SSE-сообщений для конкретного пользователя.
        """
        channel = f"user.{user_id}"
        queue = self.event_bus.subscribe(channel)
        logger.info(f"[NotificationManager] User {user_id} connected to SSE stream")

        try:
            # Начальный пинг при подключении
            yield f"event: ping\ndata: {json.dumps({'status': 'connected'})}\n\n"

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25.0)
                    event_type = event.get("type", "message")
                    yield f"event: {event_type}\ndata: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    # Keep-alive heartbeat
                    yield ": keep-alive\n\n"
        except asyncio.CancelledError:
            logger.info(f"[NotificationManager] User {user_id} SSE connection closed")
            raise
        finally:
            self.event_bus.unsubscribe(channel, queue)
