import asyncio
import time
import unittest

from core.spatial import spatial_encoder, SpatialBox
from core.tasks.payloads import BookedTicketPayload
from saas.service.bus import InMemoryEventBus
from saas.service.allocation import (
    ActiveUserTask,
    FairShareAllocationStrategy,
    AllocationManager,
)
from saas.service.notifications import NotificationManager


class TestServiceLayer(unittest.IsolatedAsyncioTestCase):
    async def test_in_memory_event_bus(self):
        bus = InMemoryEventBus()
        q1 = bus.subscribe("user.alice")
        q2 = bus.subscribe("user.alice")

        await bus.publish("user.alice", {"type": "test_event", "data": 123})

        msg1 = q1.get_nowait()
        msg2 = q2.get_nowait()
        self.assertEqual(msg1["data"], 123)
        self.assertEqual(msg2["data"], 123)

        bus.unsubscribe("user.alice", q1)
        await bus.publish("user.alice", {"type": "second_event"})
        self.assertTrue(q1.empty())
        self.assertFalse(q2.empty())

    async def test_fair_share_strategy(self):
        strategy = FairShareAllocationStrategy()
        payload = BookedTicketPayload(
            event_id="e1",
            ticket_id="t1",
            price_id="p1",
            price_value=50.0,
            seat_info="",
            session_cookies={},
            booked_at=time.time(),
            expires_at=time.time() + 600,
        )

        t1 = ActiveUserTask(task_id="t1", user_id="u1", event_id="e1", target_tickets=2, booked_count=1, priority_level=0, created_at=100.0)
        t2 = ActiveUserTask(task_id="t2", user_id="u2", event_id="e1", target_tickets=2, booked_count=0, priority_level=0, created_at=105.0)
        t3_vip = ActiveUserTask(task_id="t3", user_id="u3", event_id="e1", target_tickets=2, booked_count=0, priority_level=10, created_at=110.0)

        # VIP wins first even if created later
        winner = strategy.select_recipient([t1, t2, t3_vip], payload)
        self.assertEqual(winner.task_id, "t3")

        # Between t1 (booked=1) and t2 (booked=0), t2 wins (least booked)
        winner2 = strategy.select_recipient([t1, t2], payload)
        self.assertEqual(winner2.task_id, "t2")

    async def test_allocation_manager_spatial_matching(self):
        bus = InMemoryEventBus()
        allocator = AllocationManager(event_bus=bus)

        # Box for sector 5, rows 1..10
        box_sector_5 = spatial_encoder.encode(SpatialBox(location_id=5, row_start=1, row_count=10))
        task_sec5 = ActiveUserTask(
            task_id="task_sec5",
            user_id="user_sec5",
            event_id="concert_1",
            target_tickets=1,
            filter_boxes=(box_sector_5,),
        )

        # Box for sector 8
        box_sector_8 = spatial_encoder.encode(SpatialBox(location_id=8))
        task_sec8 = ActiveUserTask(
            task_id="task_sec8",
            user_id="user_sec8",
            event_id="concert_1",
            target_tickets=1,
            filter_boxes=(box_sector_8,),
        )

        await allocator.register_task(task_sec5)
        await allocator.register_task(task_sec8)

        user_sub = bus.subscribe("user.user_sec5")

        payload = BookedTicketPayload(
            event_id="concert_1",
            ticket_id="ticket_in_sec5",
            price_id="p10",
            price_value=35.0,
            seat_info="Сектор 5, Ряд 3, Место 7",
            session_cookies={"sess": "abc"},
            booked_at=time.time(),
            expires_at=time.time() + 600,
        )

        # Allocate ticket in sector 5, row 3, seat 7
        winner = await allocator.allocate_ticket(
            payload,
            location_id=5,
            row=3,
            seat=7,
        )

        self.assertIsNotNone(winner)
        self.assertEqual(winner.task_id, "task_sec5")
        self.assertEqual(winner.booked_count, 1)

        # Verify event emitted into user channel
        event = user_sub.get_nowait()
        self.assertEqual(event["type"], "booking_allocated")
        self.assertEqual(event["ticket_id"], "ticket_in_sec5")
        self.assertTrue(event["task_completed"])

    async def test_notification_manager_sse_stream(self):
        bus = InMemoryEventBus()
        notif_mgr = NotificationManager(event_bus=bus)

        stream = notif_mgr.stream_user_events("alice")

        # 1. Initial ping
        first_chunk = await stream.__anext__()
        self.assertIn("event: ping", first_chunk)

        # 2. Publish event to alice
        await bus.publish("user.alice", {"type": "booking_allocated", "ticket_id": "1001"})

        second_chunk = await stream.__anext__()
        self.assertIn("event: booking_allocated", second_chunk)
        self.assertIn('"ticket_id": "1001"', second_chunk)

        await stream.aclose()


if __name__ == "__main__":
    unittest.main()
