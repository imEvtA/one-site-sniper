

import asyncio
import httpx
import logging

from .tasks import Fetcher, Consumer, AtomicCounter, Parser, BaseParser, Ticket, EVENT_ID


from typing import Any, Callable, Awaitable
import inspect


logger = logging.getLogger("core.runner")


class Core:
    def __init__(
        self,
        event_id: str = EVENT_ID,
        target_tickets: int = 1,
        num_consumers: int = 5,
        parser: BaseParser | None = None,
        config: Any | None = None,
        event_callback: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.event_id = event_id
        self.target_tickets = target_tickets
        self.num_consumers = num_consumers
        self.parser = parser or Parser()
        self.config = config
        self.fetcher = Fetcher(event_id=self.event_id, parser=self.parser, config=self.config)
        self.event_callback = event_callback

    async def _emit_event(self, event_type: str, data: dict[str, Any]) -> None:
        if self.event_callback:
            payload = {"type": event_type, "event_id": self.event_id, **data}
            try:
                res = self.event_callback(payload)
                if inspect.isawaitable(res):
                    await res
            except Exception as e:
                logger.warning(f"Error in event_callback: {e}")

    async def run(
        self,
        get_client: httpx.AsyncClient | None = None,
        post_client: httpx.AsyncClient | None = None,
        initial_cookies: dict[str, str] | None = None,
        initial_headers: dict[str, str] | None = None,
    ) -> int:
        valid_prices_preview = getattr(self.parser, "valid_price_ids", None)
        allowed_prices_preview = getattr(self.parser, "allowed_price_ids", None)
        allowed_sectors_preview = getattr(self.parser, "allowed_sectors", None)

        logger.info(
            f"[Core] Starting hunt for event {self.event_id} | Target: {self.target_tickets} | "
            f"Consumers: {self.num_consumers} | Filters: valid_prices={valid_prices_preview}, "
            f"allowed_prices={allowed_prices_preview}, sectors={allowed_sectors_preview}"
        )
        await self._emit_event("status", {"message": "Initializing session..."})

        manage_get_client = get_client is None
        manage_post_client = post_client is None

        active_get_client = get_client or httpx.AsyncClient(base_url=self.fetcher.event_url)

        if initial_cookies:
            cookies = initial_cookies
            headers = initial_headers or self.fetcher.headers_template.copy()
            svg_url = await self.fetcher.get_svg_url(client=active_get_client)
            if not svg_url:
                if manage_get_client:
                    await active_get_client.aclose()
                logger.error(f"[Core] Failed to get SVG URL with initial session for event {self.event_id}")
                await self._emit_event("error", {"message": "Failed to get SVG scheme URL"})
                return 0

            # Если каталог цен еще не был загружен, загружаем его
            if self.config and not self.config.event_prices:
                await self.fetcher.fetch_prices(client=active_get_client)
        else:
            init_data = await self.fetcher.start(client=active_get_client)
            if not init_data:
                if manage_get_client:
                    await active_get_client.aclose()
                logger.error(f"[Core] Failed to initialize session in Fetcher for event {self.event_id}")
                await self._emit_event("error", {"message": "Failed to initialize session"})
                return 0
            cookies, headers, svg_url = init_data

        logger.info(f"[Core] Session ready. SVG URL: {svg_url}")
        await self._emit_event("session_initialized", {"svg_url": svg_url})

        queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()
        counter = AtomicCounter(target=self.target_tickets)
        consumer = Consumer(cookies=cookies, headers=headers)

        active_post_client = post_client or httpx.AsyncClient(cookies=cookies, headers=headers)

        async def handle_ticket_booked(ticket_id: str, price_id: str, raw_resp: dict):
            logger.info(f"[Core] 🎉 Ticket booked: {ticket_id} (price_id: {price_id})! Total: {counter.value}/{self.target_tickets}")
            await self._emit_event("ticket_booked", {
                "ticket_id": ticket_id,
                "price_id": price_id,
                "booked": counter.value,
                "target": self.target_tickets,
            })

        attempted_tickets: set[str] = set()
        loop_iteration = 0

        try:
            consumer_tasks = [
                asyncio.create_task(
                    consumer.consume(
                        counter=counter,
                        queue=queue,
                        client=active_post_client,
                        on_book_callback=handle_ticket_booked
                    )
                )
                for _ in range(self.num_consumers)
            ]

            while not await counter.is_completed():
                loop_iteration += 1
                logger.info(f"[Core] Sniper iteration #{loop_iteration} (Progress: {counter.value}/{self.target_tickets})...")
                await self._emit_event("status", {
                    "message": f"Sniper hunting (iteration #{loop_iteration}, booked: {counter.value}/{self.target_tickets})..."
                })

                # Fetch and parse available tickets
                tickets = await self.fetcher.get_tickets(
                    svg_url=svg_url,
                    client=active_get_client,
                )

                new_tickets_count = 0
                for ticket in tickets:
                    if ticket.ticket_id not in attempted_tickets:
                        attempted_tickets.add(ticket.ticket_id)
                        await queue.put((ticket.ticket_id, ticket.price_id))
                        new_tickets_count += 1

                logger.info(f"[Core] Iteration #{loop_iteration}: {len(tickets)} available tickets matched filters ({new_tickets_count} new queued)")

                if new_tickets_count > 0:
                    await self._emit_event("tickets_streamed", {
                        "found_count": new_tickets_count,
                        "booked": counter.value,
                        "target": self.target_tickets
                    })

                if await counter.is_completed():
                    break

                # Wait before next check
                await asyncio.sleep(1.0)

            for _ in range(self.num_consumers):
                await queue.put(None)

            await asyncio.gather(*consumer_tasks)

        except asyncio.CancelledError:
            logger.info(f"[Core] Hunting loop cancelled for event {self.event_id}")
            for t in consumer_tasks:
                if not t.done():
                    t.cancel()
            raise
        finally:
            if manage_get_client:
                await active_get_client.aclose()
            if manage_post_client:
                await active_post_client.aclose()

        logger.info(f"[Core] Hunt finished. Booked {counter.value}/{self.target_tickets} tickets.")
        await self._emit_event("finished", {"booked": counter.value, "target": self.target_tickets})
        return counter.value





def main() -> None:
    logging.basicConfig(level=logging.INFO)
    core = Core(event_id=EVENT_ID, target_tickets=1, num_consumers=3)
    asyncio.run(core.run())


if __name__ == "__main__":
    main()