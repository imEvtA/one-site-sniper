import asyncio
import time
import unittest
from typing import Any, Callable

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from core.tasks.payloads import BookedTicketPayload, FilterSnapshot
from saas.gateway.auth import (
    CompositeTokenHandler,
    HmacCookieTokenHandler,
    ServiceSecretTokenHandler,
)
from saas.gateway.orchestrator import SaaSGatewayOrchestrator
from saas.gateway.routes import router


class MockBotSession:
    def __init__(self, event_id: str, on_payload: Callable[[BookedTicketPayload], Any]):
        self.event_id = event_id
        self.on_payload = on_payload
        self.snapshot = FilterSnapshot()
        self.is_running = False

    def update_filter_snapshot(self, snapshot: FilterSnapshot):
        self.snapshot = snapshot

    async def start(self):
        self.is_running = True

    async def stop(self):
        self.is_running = False

    async def emit_mock_ticket(self, ticket_id: str = "t_101", loc_id: int = 5, row: int = 2, seat: int = 8):
        payload = BookedTicketPayload(
            event_id=self.event_id,
            ticket_id=ticket_id,
            price_id="p_1",
            price_value=25.0,
            seat_info=f"1:A/2:{loc_id}/3:{row}/4:{seat}",
            session_cookies={"PHPBACKSESSID": f"sess_{ticket_id}"},
            booked_at=time.time(),
            expires_at=time.time() + 600,
            location_id=loc_id,
            row=row,
            seat=seat,
        )
        res = self.on_payload(payload)
        if asyncio.iscoroutine(res):
            await res


class TestGatewayLayer(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db_url = "sqlite+aiosqlite:///:memory:"
        self.mock_sessions: dict[str, MockBotSession] = {}

        def mock_session_factory(event_id: str, on_payload: Callable[[BookedTicketPayload], Any]):
            sess = MockBotSession(event_id, on_payload)
            self.mock_sessions[event_id] = sess
            return sess

        self.orchestrator = SaaSGatewayOrchestrator(
            db_url=self.db_url,
            session_factory=mock_session_factory,
        )
        await self.orchestrator.start()

        # FastAPI test app
        self.app = FastAPI()
        self.app.state.gateway_orchestrator = self.orchestrator
        self.app.include_router(router)

    async def asyncTearDown(self):
        await self.orchestrator.shutdown()

    def test_token_handlers(self):
        # 1. HMAC Token Handler
        hmac_h = HmacCookieTokenHandler(secret_key="my_secret_key")
        token = hmac_h.create_token(user_id="user_123", role_name="USER")
        creds = hmac_h.verify_token(token)
        self.assertIsNotNone(creds)
        self.assertEqual(creds.user_id, "user_123")
        self.assertEqual(creds.role_name, "USER")

        # Tampering check
        tampered_token = token[:-4] + "abcd"
        self.assertIsNone(hmac_h.verify_token(tampered_token))

        # 2. Service Token Handler
        srv_h = ServiceSecretTokenHandler(service_secrets={"telegram": "tg_pass_123"})
        srv_token = srv_h.create_token(user_id="tg_user_999", role_name="VIP", service_name="telegram")
        srv_creds = srv_h.verify_token(srv_token)
        self.assertIsNotNone(srv_creds)
        self.assertEqual(srv_creds.user_id, "tg_user_999")
        self.assertEqual(srv_creds.role_name, "VIP")

        # 3. Composite Handler
        comp_h = CompositeTokenHandler([srv_h, hmac_h])
        self.assertIsNotNone(comp_h.verify_token(token))
        self.assertIsNotNone(comp_h.verify_token(srv_token))
        self.assertIsNone(comp_h.verify_token("invalid_token"))

    async def test_gateway_orchestrator_multi_user_flow(self):
        # User creates a task
        task = await self.orchestrator.create_user_task(
            user_id="alice",
            event_id="concert_2026",
            target_tickets=1,
            filter_boxes=[1001],
        )
        self.assertEqual(task.user_id, "alice")
        self.assertIn("concert_2026", self.mock_sessions)
        session = self.mock_sessions["concert_2026"]
        self.assertTrue(session.is_running)

        # Sniper emits ticket
        await session.emit_mock_ticket(ticket_id="ticket_alice_1", loc_id=0, row=0, seat=0)

        # Check bookings for alice
        bookings = await self.orchestrator.get_user_bookings(user_id="alice")
        self.assertEqual(len(bookings), 1)
        self.assertEqual(bookings[0].ticket_id, "ticket_alice_1")

        # Claim booking
        claimed = await self.orchestrator.claim_booking(user_id="alice", booking_id=bookings[0].booking_id)
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.ticket_id, "ticket_alice_1")

    async def test_fastapi_rest_routes(self):
        transport = ASGITransport(app=self.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Guest Auth
            resp_auth = await client.post("/api/gateway/auth/guest")
            self.assertEqual(resp_auth.status_code, 200)
            data_auth = resp_auth.json()
            user_id = data_auth["user_id"]
            token = data_auth["token"]

            headers = {"Authorization": f"Bearer {token}"}

            # 2. Create Task
            resp_task = await client.post(
                "/api/gateway/tasks",
                json={"event_id": "rock_fest", "target_tickets": 1, "filter_boxes": []},
                headers=headers,
            )
            self.assertEqual(resp_task.status_code, 200)
            task_id = resp_task.json()["task_id"]

            # 3. Get Tasks
            resp_get_tasks = await client.get("/api/gateway/tasks", headers=headers)
            self.assertEqual(resp_get_tasks.status_code, 200)
            self.assertEqual(len(resp_get_tasks.json()), 1)

            # 4. Simulate Hunt Hit
            session = self.mock_sessions["rock_fest"]
            await session.emit_mock_ticket(ticket_id="rock_t1")

            # 5. Get Bookings
            resp_b = await client.get("/api/gateway/bookings", headers=headers)
            self.assertEqual(resp_b.status_code, 200)
            bookings = resp_b.json()
            self.assertEqual(len(bookings), 1)
            b_id = bookings[0]["booking_id"]

            # 6. Claim Booking
            resp_claim = await client.post(
                "/api/gateway/bookings/claim",
                json={"booking_id": b_id},
                headers=headers,
            )
            self.assertEqual(resp_claim.status_code, 200)
            self.assertEqual(resp_claim.json()["ticket_id"], "rock_t1")

            # 7. Cancel Task
            resp_cancel = await client.delete(f"/api/gateway/tasks/{task_id}", headers=headers)
            self.assertEqual(resp_cancel.status_code, 200)
            self.assertEqual(resp_cancel.json()["status"], "cancelled")

    async def test_rbac_task_limit(self):
        transport = ASGITransport(app=self.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp_auth = await client.post("/api/gateway/auth/guest")
            token = resp_auth.json()["token"]
            headers = {"Authorization": f"Bearer {token}"}

            # GUEST role has max_active_tasks = 1
            r1 = await client.post("/api/gateway/tasks", json={"event_id": "e1", "target_tickets": 10}, headers=headers)
            self.assertEqual(r1.status_code, 200)

            # Second task should fail with 400 Bad Request
            r2 = await client.post("/api/gateway/tasks", json={"event_id": "e2", "target_tickets": 10}, headers=headers)
            self.assertEqual(r2.status_code, 400)
            self.assertIn("Task limit exceeded", r2.json()["detail"])

    async def test_sse_stream_generator(self):
        # Directly test stream generator
        stream = self.orchestrator.stream_user_events("user_stream_test")
        first_event = await anext(stream)
        self.assertIn("ping", first_event)
        self.assertIn("connected", first_event)


if __name__ == "__main__":
    unittest.main()
