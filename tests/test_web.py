import unittest
from unittest.mock import AsyncMock, patch
import httpx
from fastapi.testclient import TestClient

from web.server import app
from core.bot import bot_manager, PresessionData


class TestWebServer(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_static_assets_served(self):
        resp_css = self.client.get("/proxy-static/overlay.css")
        self.assertEqual(resp_css.status_code, 200)
        self.assertIn("#tp-sniper-widget", resp_css.text)

        resp_js = self.client.get("/proxy-static/overlay.js")
        self.assertEqual(resp_js.status_code, 200)
        self.assertIn("Fast Sniper", resp_js.text)

    def test_bot_status_idle(self):
        resp = self.client.get("/api/bot/status?event_id=99999")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("status"), "idle")

    @patch("core.pipeline.steps.scheme_resolution.Fetcher.fetch_scheme_url", new_callable=AsyncMock)
    def test_bot_start_and_stop(self, mock_fetch_scheme):
        mock_fetch_scheme.return_value = "https://auth.ticketpro.by/ticket/file/temp/mock.svg"

        start_payload = {
            "event_id": "mock_event_123",
            "target_tickets": 1,
            "num_consumers": 2,
            "allowed_price_ids": ["135001"]
        }
        resp_start = self.client.post("/api/bot/start", json=start_payload)
        self.assertEqual(resp_start.status_code, 200)
        self.assertEqual(resp_start.json().get("status"), "ok")

        resp_status = self.client.get("/api/bot/status?event_id=mock_event_123")
        self.assertEqual(resp_status.status_code, 200)
        self.assertIn(resp_status.json().get("status"), ["running", "finished", "stopped", "error"])

        resp_stop = self.client.post("/api/bot/stop", json={"event_id": "mock_event_123"})
        self.assertEqual(resp_stop.status_code, 200)
        self.assertEqual(resp_stop.json().get("status"), "ok")

    def test_bot_start_rejected_on_non_200(self):
        # When page status is 404
        start_payload = {
            "event_id": "invalid_404_event",
            "target_tickets": 1,
            "page_status": 404
        }
        resp = self.client.post("/api/bot/start", json=start_payload)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json().get("status"), "error")
        self.assertEqual(resp.json().get("error", {}).get("code"), "EVENT_NOT_FOUND")

        # Verify bot was NOT kept in bot_manager
        self.assertIsNone(bot_manager.get("invalid_404_event"))

    def test_bot_tasks_and_session_activation(self):
        resp = self.client.get("/api/bot/tasks")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("total_booked", data)
        self.assertIn("tasks", data)

        resp_act = self.client.post("/api/bot/activate-session", json={"event_id": "nonexistent"})
        self.assertEqual(resp_act.status_code, 404)

    @patch("core.pipeline.steps.scheme_resolution.Fetcher.fetch_scheme_url", new_callable=AsyncMock)
    def test_bot_start_with_price_range(self, mock_fetch_scheme):
        mock_fetch_scheme.return_value = "https://auth.ticketpro.by/ticket/file/temp/mock.svg"

        # Предсессия с ценами
        bot_manager.presessions["mock_event_price_range"] = PresessionData(
            event_id="mock_event_price_range",
            event_name="Mock Range",
            prices=[
                {"id": "135001", "price": 150.0},
                {"id": "135002", "price": 250.0},
                {"id": "135003", "price": 400.0},
            ],
            valid_price_ids=["135001", "135002", "135003"],
        )

        payload = {
            "event_id": "mock_event_price_range",
            "target_tickets": 2,
            "min_price": 100.0,
            "max_price": 300.0,
            "allowed_price_ids": ["135001", "135002"]
        }
        resp = self.client.post("/api/bot/start", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("status"), "ok")

        resp_status = self.client.get("/api/bot/status?event_id=mock_event_price_range")
        self.assertEqual(resp_status.status_code, 200)
        data = resp_status.json()
        self.assertEqual(set(data.get("valid_price_ids", [])), {"135001", "135002"})


        # Stop
        self.client.post("/api/bot/stop", json={"event_id": "mock_event_price_range"})

    def test_get_event_prices_and_presession_via_bot(self):
        bot_manager.presessions["seeded_event_123"] = PresessionData(
            event_id="seeded_event_123",
            event_name="Seeded Concert",
            prices=[
                {"id": "135001", "price": 250.0, "color": "#ff0000"},
                {"id": "135002", "price": 150.0, "color": "#00ff00"},
            ],
            valid_price_ids=["135001", "135002"],
            csrf_token="test_token_csrf",
        )

        resp_pre = self.client.get("/api/bot/presession?event_id=seeded_event_123")
        self.assertEqual(resp_pre.status_code, 200)
        pre_data = resp_pre.json()
        self.assertEqual(pre_data.get("status"), "ok")
        self.assertEqual(len(pre_data.get("prices")), 2)
        self.assertTrue(pre_data.get("has_csrf"))

        resp = self.client.get("/api/bot/event-prices?event_id=seeded_event_123")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("status"), "ok")
        self.assertIn("135001", data.get("prices", {}))
        self.assertIn("135002", data.get("prices", {}))


if __name__ == "__main__":
    unittest.main()
