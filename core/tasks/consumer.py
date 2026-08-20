import asyncio
import inspect
import logging
from typing import Any, Callable

import httpx

try:
    from .utils.constants import PARAMS_TEMPLATE, POST_URL
except ImportError:
    from utils.constants import PARAMS_TEMPLATE, POST_URL

logger = logging.getLogger("core.consumer")


class AtomicCounter:
    """
    Потокобезопасный (в рамках Event Loop) счетчик успешных бронирований.
    """
    def __init__(self, target: int = 1) -> None:
        self.target = target
        self._count = 0
        self._lock = asyncio.Lock()

    @property
    def value(self) -> int:
        return self._count

    async def try_acquire_slot(self) -> bool:
        async with self._lock:
            if self._count < self.target:
                self._count += 1
                return True
            return False

    async def release_slot(self) -> None:
        async with self._lock:
            if self._count > 0:
                self._count -= 1

    async def is_completed(self) -> bool:
        async with self._lock:
            return self._count >= self.target


class Consumer:
    """
    Низкоуровневый I/O клиент для выполнения POST-запроса резервации конкретного билета.
    """
    def __init__(
        self,
        cookies: dict[str, str],
        headers: dict[str, str],
        post_url: str = POST_URL,
        params_template: dict[str, Any] | None = None
    ) -> None:
        self.cookies = cookies
        self.headers = headers
        self.post_url = post_url
        self.params_template = (params_template or PARAMS_TEMPLATE).copy()

    def shutdown(self) -> None:
        """Индивидуальный shutdown (noop, так как оркестрацией управляет ConsumerPool)."""
        pass

    async def book(
        self,
        params: dict[str, Any],
        client: httpx.AsyncClient | None = None
    ) -> dict[str, Any]:
        if client is not None:
            resp = await client.post(self.post_url, params=params)
        else:
            async with httpx.AsyncClient(cookies=self.cookies, headers=self.headers) as c:
                resp = await c.post(self.post_url, params=params)

        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}", "status_code": resp.status_code}

        try:
            return resp.json()
        except Exception as e:
            return {"error": str(e), "raw": resp.text}

    async def consume(
        self,
        counter: AtomicCounter,
        queue: asyncio.Queue[tuple[str, str] | None],
        client: httpx.AsyncClient | None = None,
        on_book_callback: Any = None
    ) -> None:
        """
        Совместимость: выполнение единичного воркера.
        """
        own_client = False
        if client is None:
            client = httpx.AsyncClient(cookies=self.cookies, headers=self.headers)
            own_client = True

        try:
            while not await counter.is_completed():
                ticket_data = await queue.get()
                if ticket_data is None:
                    queue.task_done()
                    break

                ticket_id, price_id = ticket_data
                if not await counter.try_acquire_slot():
                    queue.task_done()
                    break

                params = self.params_template.copy()
                params["ticket_id"] = ticket_id
                params["price_id"] = price_id

                result = await self.book(params=params, client=client)
                if "error" in result and result.get("error") is not None:
                    await counter.release_slot()
                else:
                    if on_book_callback:
                        res = on_book_callback(ticket_id, price_id, result)
                        if inspect.isawaitable(res):
                            await res

                queue.task_done()
        finally:
            if own_client:
                await client.aclose()


class ConsumerPool:
    """
    Оркестратор пула консьюмеров.
    Инкапсулирует создание задач, общий атомарный флаг is_running и детерминированный shutdown.
    """
    def __init__(
        self,
        num_consumers: int,
        queue: asyncio.Queue[tuple[str, str] | None],
        counter: AtomicCounter,
        cookies: dict[str, str],
        headers: dict[str, str],
        post_url: str = POST_URL,
        params_template: dict[str, Any] | None = None,
        on_book_callback: Callable[[str, str, dict[str, Any]], Any] | None = None,
    ) -> None:
        self.num_consumers = num_consumers
        self.queue = queue
        self.counter = counter
        self.on_book_callback = on_book_callback
        self.consumer = Consumer(
            cookies=cookies,
            headers=headers,
            post_url=post_url,
            params_template=params_template,
        )
        self.is_running = True
        self.stop_event = asyncio.Event()
        self._tasks: list[asyncio.Task[Any]] = []

    def start(self, client: httpx.AsyncClient) -> None:
        """
        Запускает N параллельных корутин-воркеров.
        """
        self.is_running = True
        self.stop_event.clear()
        self._tasks = [
            asyncio.create_task(self._worker(client, worker_id=i + 1))
            for i in range(self.num_consumers)
        ]
        logger.info(f"[ConsumerPool] Spawned {self.num_consumers} consumer workers.")

    async def _worker(self, client: httpx.AsyncClient, worker_id: int) -> None:
        """
        Рабочий цикл отдельного консьюмера в пуле.
        Реактивно ожидает новые элементы в очереди или взведение stop_event (без таймаут-поллинга).
        """
        try:
            while not self.stop_event.is_set() and not await self.counter.is_completed():
                get_task = asyncio.create_task(self.queue.get())
                stop_task = asyncio.create_task(self.stop_event.wait())

                try:
                    done, pending = await asyncio.wait(
                        [get_task, stop_task],
                        return_when=asyncio.FIRST_COMPLETED
                    )
                finally:
                    for p in (get_task, stop_task):
                        if not p.done():
                            p.cancel()
                            try:
                                await p
                            except (asyncio.CancelledError, Exception):
                                pass

                if self.stop_event.is_set() or stop_task in done:
                    if get_task in done and not get_task.cancelled():
                        try:
                            self.queue.task_done()
                        except ValueError:
                            pass
                    break

                try:
                    ticket_data = get_task.result()
                except (asyncio.CancelledError, Exception):
                    break

                if ticket_data is None:
                    self.queue.task_done()
                    break

                ticket_id, price_id = ticket_data

                if not await self.counter.try_acquire_slot():
                    self.queue.task_done()
                    break

                params = self.consumer.params_template.copy()
                params["ticket_id"] = ticket_id
                params["price_id"] = price_id

                logger.info(f"[Consumer #{worker_id}] Booking ticket {ticket_id} (price_id: {price_id})...")
                result = await self.consumer.book(params=params, client=client)

                if "error" in result and result.get("error") is not None:
                    logger.warning(f"[Consumer #{worker_id}] Failed to book {ticket_id}: {result.get('error')}. Releasing slot.")
                    await self.counter.release_slot()
                else:
                    logger.info(
                        f"[Consumer #{worker_id}] 🎉 Successfully booked {ticket_id}! "
                        f"(Progress: {self.counter.value}/{self.counter.target})"
                    )
                    if self.on_book_callback:
                        res = self.on_book_callback(ticket_id, price_id, result)
                        if inspect.isawaitable(res):
                            await res

                self.queue.task_done()

                if await self.counter.is_completed():
                    break

        except asyncio.CancelledError:
            logger.debug(f"[Consumer #{worker_id}] Worker task cancelled.")
            raise


    async def shutdown(self) -> None:
        """
        Детерминированная остановка всех воркеров:
        1. Взводит stop_event (немедленно будит всех воркеров).
        2. Отменяет активные корутины.
        3. Дожидается полного завершения их циклов (join/gather) с обработкой исключений.
        """
        self.is_running = False
        self.stop_event.set()
        logger.info(f"[ConsumerPool] Shutting down {len(self._tasks)} workers...")

        for t in self._tasks:
            if not t.done():
                t.cancel()

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()

        logger.info("[ConsumerPool] All workers cleanly terminated.")