import asyncio
import httpx
import logging
from typing import Any

try:
    from .utils.constants import PARAMS_TEMPLATE, POST_URL
except ImportError:
    from utils.constants import PARAMS_TEMPLATE, POST_URL


class AtomicCounter:
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

    async def book(
        self,
        params: dict[str, Any],
        client: httpx.AsyncClient | None = None
    ) -> dict:
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
        own_client = False
        if client is None:
            client = httpx.AsyncClient(cookies=self.cookies, headers=self.headers)
            own_client = True

        try:
            while True:
                if await counter.is_completed():
                    break

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

                logging.info(f"Consumer attempting to book ticket {ticket_id} (price_id: {price_id}) | Cookies: {self.cookies}...")

                result = await self.book(params=params, client=client)

                if "error" in result and result.get("error") is not None:
                    logging.warning(f"Failed to book ticket {ticket_id}: {result.get('error')}. Releasing slot.")
                    await counter.release_slot()
                else:
                    logging.info(f"Successfully booked ticket {ticket_id}! Raw Response: {result} | (Progress: {counter.value}/{counter.target})")
                    if on_book_callback:
                        import inspect
                        res = on_book_callback(ticket_id, price_id, result)
                        if inspect.isawaitable(res):
                            await res

                queue.task_done()

                if await counter.is_completed():
                    break


        finally:
            if own_client:
                await client.aclose()