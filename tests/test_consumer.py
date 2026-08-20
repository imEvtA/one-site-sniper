import unittest
import asyncio
import httpx
from core.tasks.consumer import Consumer, ConsumerPool, AtomicCounter


class TestConsumer(unittest.IsolatedAsyncioTestCase):
    async def test_atomic_counter(self):
        counter = AtomicCounter(target=2)
        self.assertEqual(counter.value, 0)
        self.assertFalse(await counter.is_completed())

        self.assertTrue(await counter.try_acquire_slot())
        self.assertEqual(counter.value, 1)

        self.assertTrue(await counter.try_acquire_slot())
        self.assertEqual(counter.value, 2)
        self.assertTrue(await counter.is_completed())

        # Cannot acquire beyond target
        self.assertFalse(await counter.try_acquire_slot())

        # Release slot on failure
        await counter.release_slot()
        self.assertEqual(counter.value, 1)
        self.assertFalse(await counter.is_completed())

    async def test_consumer_booking_success(self):
        def mock_handler(request: httpx.Request) -> httpx.Response:
            self.assertIn("/api/ticket/ticket-reserve/", str(request.url))
            return httpx.Response(200, json={"status": "ok", "error": None})

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
        consumer = Consumer(cookies={"test_cookie": "1"}, headers={"X-CSRF-Token": "tok"})
        counter = AtomicCounter(target=1)
        queue = asyncio.Queue()

        # Push one ticket and one sentinel
        await queue.put(("ticket_100", "price_200"))
        await queue.put(None)

        await consumer.consume(counter=counter, queue=queue, client=mock_client)
        self.assertEqual(counter.value, 1)
        self.assertTrue(await counter.is_completed())
        await mock_client.aclose()

    async def test_consumer_booking_retry_on_failure(self):
        # First ticket fails (already taken), second ticket succeeds
        attempts = 0

        def mock_handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(200, json={"error": "Place already reserved"})
            return httpx.Response(200, json={"status": "ok", "error": None})

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
        consumer = Consumer(cookies={}, headers={})
        counter = AtomicCounter(target=1)
        queue = asyncio.Queue()

        await queue.put(("bad_ticket", "100"))
        await queue.put(("good_ticket", "100"))
        await queue.put(None)

        await consumer.consume(counter=counter, queue=queue, client=mock_client)
        self.assertEqual(counter.value, 1)
        self.assertEqual(attempts, 2)
        await mock_client.aclose()

    async def test_consumer_pool_orchestration_and_shutdown(self):
        booked = []

        def mock_handler(request: httpx.Request) -> httpx.Response:
            tid = request.url.params.get("ticket_id")
            booked.append(tid)
            return httpx.Response(200, json={"status": "ok", "error": None})

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
        counter = AtomicCounter(target=3)
        queue = asyncio.Queue()

        pool = ConsumerPool(
            num_consumers=3,
            queue=queue,
            counter=counter,
            cookies={"a": "b"},
            headers={"c": "d"},
        )
        pool.start(client=mock_client)

        for i in range(3):
            await queue.put((f"ticket_{i}", f"price_{i}"))

        # Wait briefly for workers to consume
        await asyncio.sleep(0.1)
        self.assertEqual(counter.value, 3)
        self.assertEqual(len(booked), 3)

        # Test clean shutdown
        await pool.shutdown()
        self.assertFalse(pool.is_running)
        self.assertEqual(len(pool._tasks), 0)

        await mock_client.aclose()


if __name__ == "__main__":
    unittest.main()

