import asyncio
import inspect
import logging
import re
import time
from typing import Any, Callable

import httpx

try:
    from .utils.constants import PARAMS_TEMPLATE, POST_URL
    from .payloads import BookedTicketPayload
    from .session_provider import BaseSessionProvider
    from .base_consumer import BaseConsumer
except ImportError:
    from utils.constants import PARAMS_TEMPLATE, POST_URL
    from core.tasks.payloads import BookedTicketPayload
    from core.tasks.session_provider import BaseSessionProvider
    from core.tasks.base_consumer import BaseConsumer

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
        """Атомарно захватывает слот и возвращает True в случае успеха."""
        async with self._lock:
            if self._count < self.target:
                self._count += 1
                return True
            return False

    async def set_target(self, new_target: int) -> None:
        async with self._lock:
            self.target = new_target

    async def release_slot(self) -> None:
        async with self._lock:
            if self._count > 0:
                self._count -= 1

    async def is_completed(self) -> bool:
        async with self._lock:
            return self._count >= self.target


class Consumer(BaseConsumer):
    """
    Низкоуровневый I/O клиент для выполнения POST-запроса резервации конкретного билета.
    """
    def __init__(
        self,
        cookies: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        post_url: str = POST_URL,
        params_template: dict[str, Any] | None = None
    ) -> None:
        self.cookies = cookies or {}
        self.headers = headers or {}
        self.post_url = post_url
        self.params_template = (params_template or PARAMS_TEMPLATE).copy()

    def shutdown(self) -> None:
        pass

    async def book(
        self,
        params: dict[str, Any],
        client: httpx.AsyncClient | None = None,
        cookies: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        use_cookies = cookies or self.cookies
        use_headers = headers or self.headers

        if client is not None:
            resp = await client.post(self.post_url, params=params, cookies=use_cookies, headers=use_headers)
        else:
            async with httpx.AsyncClient(cookies=use_cookies, headers=use_headers) as c:
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
    Инкапсулирует создание задач, общий атомарный флаг is_running, интеграцию с SessionProvider
    и детерминированный shutdown.
    """
    def __init__(
        self,
        num_consumers: int,
        queue: asyncio.Queue[tuple[str, str] | None],
        counter: AtomicCounter,
        cookies: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        post_url: str = POST_URL,
        params_template: dict[str, Any] | None = None,
        on_book_callback: Callable[[str, str, dict[str, Any]], Any] | None = None,
        session_provider: BaseSessionProvider | None = None,
        on_payload_callback: Callable[[BookedTicketPayload], Any] | None = None,
        event_id: str = "",
        consumer: BaseConsumer | None = None,
    ) -> None:
        self.num_consumers = num_consumers
        self.queue = queue
        self.counter = counter
        self.on_book_callback = on_book_callback
        self.on_payload_callback = on_payload_callback
        self.session_provider = session_provider
        self.event_id = str(event_id)
        self.cookies = cookies or {}
        self.headers = headers or {}
        self.consumer = consumer or Consumer(
            cookies=self.cookies,
            headers=self.headers,
            post_url=post_url,
            params_template=params_template,
        )
        self.is_running = True
        self.stop_event = asyncio.Event()
        self._tasks: list[asyncio.Task[Any]] = []

    def start(self, client: httpx.AsyncClient | None = None) -> None:
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

    async def _worker(self, client: httpx.AsyncClient | None, worker_id: int) -> None:
        """
        Рабочий цикл отдельного консьюмера в пуле.
        Реактивно ожидает новые элементы в очереди или взведение stop_event.
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

                params = self.consumer.params_template.copy() if hasattr(self.consumer, "params_template") else {}
                params["ticket_id"] = ticket_id
                params["price_id"] = price_id

                # Получение сессии из провайдера (если подключен) или fallback
                if self.session_provider:
                    active_cookies, active_headers = await self.session_provider.get_session()
                else:
                    active_cookies, active_headers = self.cookies.copy(), self.headers.copy()

                logger.info(f"[Consumer #{worker_id}] Booking ticket {ticket_id} (price_id: {price_id})...")
                result = await self.consumer.book(
                    params=params,
                    client=client,
                    cookies=active_cookies,
                    headers=active_headers,
                )

                if "error" in result and result.get("error") is not None:
                    logger.warning(f"[Consumer #{worker_id}] Failed to book {ticket_id}: {result.get('error')}. Releasing slot.")
                    await self.counter.release_slot()
                    if self.session_provider and result.get("status_code") in (403, 429):
                        await self.session_provider.report_invalid(active_cookies)
                else:
                    booked_at = time.time()
                    expires_at = booked_at + 600.0  # Ровно 10 минут
                    logger.info(
                        f"[Consumer #{worker_id}] 🎉 Successfully booked {ticket_id}! "
                        f"(Progress: {self.counter.value}/{self.counter.target})"
                    )

                    seat_name = str(result.get("name", "") or "")
                    loc_id = 0
                    row_num = 0
                    seat_num = 0

                    if seat_name:
                        # Extract sector (2:...), row (3:...), seat (4:...)
                        m_loc = re.search(r"2:([^/]+)", seat_name)
                        m_row = re.search(r"3:([^/]+)", seat_name)
                        m_seat = re.search(r"4:([^/]+)", seat_name)
                        if m_loc and m_loc.group(1).isdigit():
                            loc_id = int(m_loc.group(1))
                        if m_row and m_row.group(1).isdigit():
                            row_num = int(m_row.group(1))
                        if m_seat and m_seat.group(1).isdigit():
                            seat_num = int(m_seat.group(1))

                    if self.session_provider:
                        await self.session_provider.report_used(active_cookies)

                    if self.on_payload_callback:
                        payload = BookedTicketPayload(
                            event_id=self.event_id,
                            ticket_id=ticket_id,
                            price_id=price_id,
                            price_value=float(result.get("price", 0.0) or 0.0),
                            seat_info=seat_name,
                            session_cookies=active_cookies,
                            booked_at=booked_at,
                            expires_at=expires_at,
                            location_id=loc_id,
                            row=row_num,
                            seat=seat_num,
                            raw_response=result,
                        )
                        try:
                            res_p = self.on_payload_callback(payload)
                            if inspect.isawaitable(res_p):
                                await res_p
                        except Exception as e:
                            logger.exception(f"[Consumer #{worker_id}] Error in on_payload_callback: {e}")

                    if self.on_book_callback:
                        try:
                            res = self.on_book_callback(ticket_id, price_id, result)
                            if inspect.isawaitable(res):
                                await res
                        except Exception as e:
                            logger.exception(f"[Consumer #{worker_id}] Error in on_book_callback: {e}")

                self.queue.task_done()

                if await self.counter.is_completed():
                    break

        except asyncio.CancelledError:
            logger.debug(f"[Consumer #{worker_id}] Worker task cancelled.")
            raise

    async def shutdown(self) -> None:
        """
        Детерминированная остановка всех воркеров.
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