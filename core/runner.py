

import asyncio
import httpx
import logging

from .tasks import Fetcher, Consumer, AtomicCounter, Parser, BaseParser, Ticket, EVENT_ID


from typing import Any, Callable, Awaitable
import inspect


class Core:
    def __init__(
        self,
        event_id: str = EVENT_ID,
        target_tickets: int = 1,
        num_consumers: int = 5,
        parser: BaseParser | None = None,
        event_callback: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.event_id = event_id
        self.target_tickets = target_tickets
        self.num_consumers = num_consumers
        self.parser = parser or Parser()
        self.fetcher = Fetcher(event_id=self.event_id, parser=self.parser)
        self.event_callback = event_callback

    async def _emit_event(self, event_type: str, data: dict[str, Any]) -> None:
        if self.event_callback:
            payload = {"type": event_type, "event_id": self.event_id, **data}
            try:
                res = self.event_callback(payload)
                if inspect.isawaitable(res):
                    await res
            except Exception as e:
                logging.warning(f"Error in event_callback: {e}")

    async def run(
        self,
        get_client: httpx.AsyncClient | None = None,
        post_client: httpx.AsyncClient | None = None,
        initial_cookies: dict[str, str] | None = None,
        initial_headers: dict[str, str] | None = None,
    ) -> int:
        logging.info("Starting Core orchestrator...")
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
                logging.error("Failed to get SVG URL with initial session")
                await self._emit_event("error", {"message": "Failed to get SVG scheme URL"})
                return 0
        else:
            init_data = await self.fetcher.start(client=active_get_client)
            if not init_data:
                if manage_get_client:
                    await active_get_client.aclose()
                logging.error("Failed to initialize session in Fetcher")
                await self._emit_event("error", {"message": "Failed to initialize session"})
                return 0
            cookies, headers, svg_url = init_data

        logging.info(f"Session initialized. SVG URL: {svg_url}")
        await self._emit_event("session_initialized", {"svg_url": svg_url})


        queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()
        counter = AtomicCounter(target=self.target_tickets)
        consumer = Consumer(cookies=cookies, headers=headers)

        active_post_client = post_client or httpx.AsyncClient(cookies=cookies, headers=headers)

        async def handle_ticket_booked(ticket_id: str, price_id: str, raw_resp: dict):
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
                logging.info(f"Sniper hunting iteration #{loop_iteration} (Progress: {counter.value}/{self.target_tickets})...")
                await self._emit_event("status", {
                    "message": f"Sniper hunting (iteration #{loop_iteration}, booked: {counter.value}/{self.target_tickets})..."
                })

                # Fetch available tickets matching filters
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

        finally:
            if manage_get_client:
                await active_get_client.aclose()
            if manage_post_client:
                await active_post_client.aclose()

        logging.info(f"Core finished. Booked {counter.value}/{self.target_tickets} tickets.")
        await self._emit_event("finished", {"booked": counter.value, "target": self.target_tickets})
        return counter.value




def main() -> None:
    logging.basicConfig(level=logging.INFO)
    core = Core(event_id=EVENT_ID, target_tickets=1, num_consumers=3)
    asyncio.run(core.run())


if __name__ == "__main__":
    main()