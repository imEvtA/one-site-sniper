import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

import httpx

from core.tasks.payloads import BookedTicketPayload, FilterSnapshot
from core.tasks.session_provider import TicketproSessionProvider, BaseSessionProvider
from core.tasks.producer import ProducerUnit
from core.tasks.parser import Ticket, DefaultParser
from core.tasks.consumer import ConsumerPool, AtomicCounter
from core.pipeline import HuntingContext
from core.bot import BotSession


class MockSessionProvider(BaseSessionProvider):
    def __init__(self):
        self.used = []
        self.invalid = []

    async def get_session(self):
        return {"PHPBACKSESSID": "mock_sess_123"}, {"X-CSRF-Token": "csrf_123"}

    async def report_used(self, cookies):
        self.used.append(cookies)

    async def report_invalid(self, cookies):
        self.invalid.append(cookies)

    async def warm_up(self, count=5):
        pass

    async def shutdown(self):
        pass


class TestEngineRefactor(unittest.IsolatedAsyncioTestCase):
    async def test_producer_unit_polling(self):
        mock_fetcher = MagicMock()
        mock_fetcher.fetch_svg = AsyncMock(return_value="<svg><g><circle id='101' fill='#ff0000'/></g></svg>")
        mock_parser = MagicMock()
        mock_parser.parse.return_value = [Ticket(ticket_id="101", price_id="p1", name="1:A/2:Sector1/3:1/4:1")]

        producer = ProducerUnit(event_id="test_event", parser=mock_parser, fetcher=mock_fetcher)
        tickets = await producer.poll_once("http://mock.svg")

        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0].ticket_id, "101")
        mock_fetcher.fetch_svg.assert_awaited_once_with("http://mock.svg", client=None)
        mock_parser.parse.assert_called_once()

    async def test_consumer_pool_with_session_provider_and_payload(self):
        provider = MockSessionProvider()
        queue = asyncio.Queue()
        counter = AtomicCounter(target=1)
        received_payloads = []

        def on_payload(payload: BookedTicketPayload):
            received_payloads.append(payload)

        pool = ConsumerPool(
            num_consumers=1,
            queue=queue,
            counter=counter,
            session_provider=provider,
            on_payload_callback=on_payload,
            event_id="evt_42",
        )

        # Mock consumer book method
        pool.consumer.book = AsyncMock(return_value={"status": "ok", "price": 45.0, "name": "Сектор А"})

        # Feed 1 ticket
        await queue.put(("ticket_99", "price_50"))

        # Start pool
        pool.start()
        await asyncio.sleep(0.05)
        await pool.shutdown()

        self.assertEqual(len(received_payloads), 1)
        p = received_payloads[0]
        self.assertEqual(p.event_id, "evt_42")
        self.assertEqual(p.ticket_id, "ticket_99")
        self.assertEqual(p.price_id, "price_50")
        self.assertEqual(p.price_value, 45.0)
        self.assertEqual(p.session_cookies, {"PHPBACKSESSID": "mock_sess_123"})
        self.assertGreater(p.expires_at, p.booked_at)
        self.assertEqual(len(provider.used), 1)

    async def test_bot_session_dynamic_filter_snapshot(self):
        ctx = HuntingContext(
            event_id="evt_10",
            event_name="Event 10",
            svg_url="http://mock.svg",
            cookies={},
            valid_price_ids={"p1"},
            allowed_sectors={"Sector A"},
            target_tickets=1,
            num_consumers=1,
            poll_interval=0.1,
            csrf_token="",
        )
        session = BotSession(ctx=ctx)
        self.assertEqual(session.parser.valid_price_ids, {"p1"})
        self.assertEqual(session.parser.allowed_sectors, {"Sector A"})

        # Update snapshot atomically
        snapshot = FilterSnapshot(
            valid_price_ids=frozenset(["p1", "p2"]),
            allowed_sectors=frozenset(["Sector A", "Sector B"]),
        )
        session.update_filter_snapshot(snapshot)

        self.assertEqual(session.parser.valid_price_ids, frozenset(["p1", "p2"]))
        self.assertEqual(session.parser.allowed_sectors, frozenset(["Sector A", "Sector B"]))


if __name__ == "__main__":
    unittest.main()
