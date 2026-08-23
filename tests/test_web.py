import unittest
from unittest.mock import AsyncMock, patch
import httpx
from fastapi.testclient import TestClient

from web.server import app


class TestWebServer(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_static_assets_served(self):
        resp_css = self.client.get("/proxy-static/overlay.css")
        self.assertEqual(resp_css.status_code, 200)
        self.assertIn("#tp-sniper-widget", resp_css.text)

        resp_js = self.client.get("/proxy-static/overlay.js")
        self.assertEqual(resp_js.status_code, 200)
        self.assertIn("Sniper Hub", resp_js.text)

    def test_checkout_redirects(self):
        for path in ["/korzina", "/korzina/", "/basket", "/basket/", "/cart", "/cart/"]:
            resp = self.client.get(path, follow_redirects=False)
            self.assertEqual(resp.status_code, 302)
            self.assertEqual(resp.headers["Location"], "/order/auth/")

    @patch("httpx.AsyncClient.request")
    def test_proxy_injects_hud_on_html(self, mock_request):
        mock_response = httpx.Response(
            status_code=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            text="<html><body><h1>Event Page</h1></body></html>",
            request=httpx.Request("GET", "https://www.ticketpro.by/kupit-bilet/48997/"),
        )
        mock_request.return_value = mock_response

        resp = self.client.get("/kupit-bilet/48997/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Ticketpro Sniper HUD", resp.text)
        self.assertIn("/proxy-static/overlay.js", resp.text)

    @patch("httpx.AsyncClient.send")
    def test_proxy_forwards_saas_gateway(self, mock_send):
        mock_response = httpx.Response(
            status_code=200,
            headers={"Content-Type": "application/json"},
            json={"user_id": "guest_123", "role": "GUEST", "token": "mock.token"},
            request=httpx.Request("POST", "http://127.0.0.1:8001/api/gateway/auth/guest"),
        )
        mock_send.return_value = mock_response

        resp = self.client.post("/api/gateway/auth/guest")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["user_id"], "guest_123")


if __name__ == "__main__":
    unittest.main()
