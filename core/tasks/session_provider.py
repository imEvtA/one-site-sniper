import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

from core.tasks.fetcher import Fetcher
from core.tasks.utils.constants import HEADERS_TEMPLATE

logger = logging.getLogger("core.session_provider")


class BaseSessionProvider(ABC):
    """
    Абстрактный контракт поставщика чистых сессий для консьюмеров.
    Изолирует консьюмеров от генерации сессий, кук и CSRF-токенов.
    """

    @abstractmethod
    async def get_session(self) -> tuple[dict[str, str], dict[str, str]]:
        """
        Возвращает готовую чистую пару (cookies, headers) для выполнения бронирования.
        """
        pass

    @abstractmethod
    async def report_used(self, cookies: dict[str, str]) -> None:
        """
        Уведомляет провайдер, что сессия была успешно использована под корзину.
        Провайдер исключает её и асинхронно пополняет пул новой чистой сессией.
        """
        pass

    @abstractmethod
    async def report_invalid(self, cookies: dict[str, str]) -> None:
        """
        Уведомляет провайдер, что сессия заблокирована/недействительна (403/429/Turnstile).
        """
        pass

    @abstractmethod
    async def warm_up(self, target_count: int = 5) -> None:
        """Предварительный прогрев пула чистых сессий."""
        pass

    @abstractmethod
    async def shutdown(self) -> None:
        """Корректное завершение провайдера."""
        pass


class TicketproSessionProvider(BaseSessionProvider):
    """
    Провайдер сессий для платформы Ticketpro.
    Асинхронно поддерживает пул чистых сессий с валидными PHPBACKSESSID и CSRF-токенами.
    """

    def __init__(
        self,
        event_id: str,
        initial_cookies: dict[str, str] | None = None,
        initial_headers: dict[str, str] | None = None,
        max_pool_size: int = 10,
    ) -> None:
        self.event_id = str(event_id)
        self.max_pool_size = max_pool_size
        self.fetcher = Fetcher(event_id=self.event_id)
        self._pool: asyncio.Queue[tuple[dict[str, str], dict[str, str]]] = asyncio.Queue(maxsize=max_pool_size)
        self._is_running = True
        self._fallback_cookies = (initial_cookies or {}).copy()
        self._fallback_headers = (initial_headers or HEADERS_TEMPLATE).copy()
        self._bg_tasks: set[asyncio.Task[Any]] = set()

        if initial_cookies:
            self._pool.put_nowait((self._fallback_cookies.copy(), self._fallback_headers.copy()))

    async def warm_up(self, target_count: int = 5) -> None:
        """
        Параллельно запрашивает чистые сессии для заполнения пула.
        """
        needed = min(target_count, self.max_pool_size) - self._pool.qsize()
        if needed <= 0:
            return

        async def _fetch_one():
            async with httpx.AsyncClient(base_url=self.fetcher.event_url, timeout=15.0) as client:
                res = await self.fetcher.fetch_page(event_id=self.event_id, client=client)
                if res:
                    _, cookies, csrf = res
                    headers = self._fallback_headers.copy()
                    if csrf:
                        headers["X-CSRF-Token"] = csrf
                    try:
                        self._pool.put_nowait((cookies, headers))
                    except asyncio.QueueFull:
                        pass

        tasks = [_fetch_one() for _ in range(needed)]
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info(f"[SessionProvider] Warmed up {self._pool.qsize()} clean sessions for event {self.event_id}")

    async def get_session(self) -> tuple[dict[str, str], dict[str, str]]:
        """
        Возвращает сессию из пула. Если пул пуст — мгновенно создает новую или отдает fallback.
        """
        try:
            return self._pool.get_nowait()
        except asyncio.QueueEmpty:
            # Если пул пуст, пробуем быстро сгенерировать новую сессию
            try:
                async with httpx.AsyncClient(base_url=self.fetcher.event_url, timeout=10.0) as client:
                    res = await self.fetcher.fetch_page(event_id=self.event_id, client=client)
                    if res:
                        _, cookies, csrf = res
                        headers = self._fallback_headers.copy()
                        if csrf:
                            headers["X-CSRF-Token"] = csrf
                        return cookies, headers
            except Exception as e:
                logger.warning(f"[SessionProvider] On-demand session generation failed: {e}")

            return self._fallback_cookies.copy(), self._fallback_headers.copy()

    async def report_used(self, cookies: dict[str, str]) -> None:
        """
        Асинхронно восполняет использованную сессию в пуле.
        """
        if self._is_running and self._pool.qsize() < self.max_pool_size:
            self._schedule_replenish()

    async def report_invalid(self, cookies: dict[str, str]) -> None:
        if self._is_running and self._pool.qsize() < self.max_pool_size:
            self._schedule_replenish()

    def _schedule_replenish(self) -> None:
        task = asyncio.create_task(self._replenish())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _replenish(self) -> None:
        try:
            async with httpx.AsyncClient(base_url=self.fetcher.event_url, timeout=10.0) as client:
                res = await self.fetcher.fetch_page(event_id=self.event_id, client=client)
                if res:
                    _, cookies, csrf = res
                    headers = self._fallback_headers.copy()
                    if csrf:
                        headers["X-CSRF-Token"] = csrf
                    await self._pool.put((cookies, headers))
        except Exception as e:
            logger.debug(f"[SessionProvider] Replenish error: {e}")

    async def shutdown(self) -> None:
        self._is_running = False
        for t in list(self._bg_tasks):
            t.cancel()
        if self._bg_tasks:
            await asyncio.gather(*self._bg_tasks, return_exceptions=True)
            self._bg_tasks.clear()

        while not self._pool.empty():
            try:
                self._pool.get_nowait()
            except asyncio.QueueEmpty:
                break
