import asyncio
import time
import unittest
import uuid

from core.tasks.payloads import BookedTicketPayload
from saas.storage.orchestrator import StorageOrchestrator


class TestStorageLayer(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # In-memory SQLite for super-fast tests
        self.db_path = f"sqlite+aiosqlite:///:memory:"
        self.storage = StorageOrchestrator(db_url=self.db_path)
        await self.storage.init_db()

    async def asyncTearDown(self):
        await self.storage.close()

    async def test_init_and_roles_seeded(self):
        user = await self.storage.get_or_create_user(user_id="alice", role_name="VIP")
        self.assertEqual(user.id, "alice")
        self.assertEqual(user.role_name, "VIP")

    async def test_task_creation_and_active_query(self):
        user = await self.storage.get_or_create_user(user_id="bob")
        task = await self.storage.create_task(
            user_id=user.id,
            event_id="concert_42",
            target_tickets=2,
            filter_boxes=[100, 200],
        )

        self.assertEqual(task.user_id, "bob")
        self.assertEqual(task.event_id, "concert_42")
        self.assertEqual(task.filter_boxes, (100, 200))

        active_tasks = await self.storage.get_active_tasks()
        self.assertEqual(len(active_tasks), 1)
        self.assertEqual(active_tasks[0].id, task.id)

    async def test_save_and_claim_booking(self):
        user = await self.storage.get_or_create_user(user_id="charlie")
        task = await self.storage.create_task(user_id=user.id, event_id="evt_1", target_tickets=1)

        payload = BookedTicketPayload(
            event_id="evt_1",
            ticket_id="t_101",
            price_id="p_1",
            price_value=40.0,
            seat_info="Ряд 1, Место 5",
            session_cookies={"PHPBACKSESSID": "sess_123"},
            booked_at=time.time(),
            expires_at=time.time() + 600,
        )

        booking = await self.storage.save_booking(task_id=task.id, user_id=user.id, payload=payload)
        self.assertEqual(booking.status, "unclaimed")
        self.assertEqual(booking.ticket_id, "t_101")

        # Check task completion in DB
        active_tasks = await self.storage.get_active_tasks()
        self.assertEqual(len(active_tasks), 0)  # completed!

        # Charlie claims booking
        claimed = await self.storage.claim_booking(booking_id=booking.id, user_id="charlie")
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.ticket_id, "t_101")
        self.assertEqual(claimed.cookies, {"PHPBACKSESSID": "sess_123"})
        self.assertGreater(claimed.time_left_sec, 0)

    async def test_expiry_cleanup_and_crash_recovery(self):
        user = await self.storage.get_or_create_user(user_id="david")
        task = await self.storage.create_task(user_id=user.id, event_id="evt_festival", target_tickets=1)

        # Save an already-expired booking
        expired_payload = BookedTicketPayload(
            event_id="evt_festival",
            ticket_id="t_old",
            price_id="p_1",
            price_value=30.0,
            seat_info="",
            session_cookies={},
            booked_at=time.time() - 700,
            expires_at=time.time() - 100,
        )
        await self.storage.save_booking(task_id=task.id, user_id=user.id, payload=expired_payload)

        # Before cleanup: 0 active tasks (marked completed)
        self.assertEqual(len(await self.storage.get_active_tasks()), 0)

        # Run crash recovery
        ram_tasks, unique_events = await self.storage.recover_on_startup()

        # Task should be restored to active since booking expired!
        self.assertEqual(len(ram_tasks), 1)
        self.assertEqual(ram_tasks[0].task_id, task.id)
        self.assertEqual(ram_tasks[0].booked_count, 0)
        self.assertEqual(unique_events, ["evt_festival"])


if __name__ == "__main__":
    unittest.main()
