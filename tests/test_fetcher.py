import unittest
import asyncio
import httpx
from core.tasks.fetcher import Fetcher
from core.tasks.parser import DefaultParser

MOCK_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta name="csrf-token" content="mock_csrf_token_12345">
</head>
<body></body>
</html>
"""

MOCK_SVG = """
<svg xmlns="http://www.w3.org/2000/svg">
  <g stroke-width="1.0" fill="#0000FF" price_id="135001">
    <circle id="1111" name="1:A/2:Sector1/3:1/4:1" />
  </g>
</svg>
"""


class TestFetcher(unittest.IsolatedAsyncioTestCase):
    async def test_fetcher_methods(self):
        def mock_handler(request: httpx.Request) -> httpx.Response:
            url_str = str(request.url)
            if "/kupit-bilet/47425/" in url_str or url_str.endswith("/47425/"):
                return httpx.Response(200, text=MOCK_HTML, headers={"set-cookie": "session_id=abc"})
            elif "get-scheme-prices-grouped" in url_str:
                return httpx.Response(200, json={"file": "temp/mock.svg"})
            elif "mock.svg" in url_str:
                return httpx.Response(200, text=MOCK_SVG)
            return httpx.Response(404)

        transport = httpx.MockTransport(mock_handler)
        fetcher = Fetcher(event_id="47425")

        mock_client = httpx.AsyncClient(base_url="https://www.ticketpro.by/kupit-bilet", transport=transport)

        # 1. Test fetch_page
        page_res = await fetcher.fetch_page(event_id="47425", client=mock_client)
        self.assertIsNotNone(page_res)
        html, cookies, csrf = page_res
        self.assertEqual(csrf, "mock_csrf_token_12345")
        self.assertEqual(cookies.get("session_id"), "abc")

        # 2. Test fetch_scheme_url
        svg_url = await fetcher.fetch_scheme_url(event_id="47425", client=mock_client)
        self.assertIsNotNone(svg_url)
        self.assertIn("mock.svg", svg_url)

        # 3. Test fetch_svg
        svg_text = await fetcher.fetch_svg(svg_url, client=mock_client)
        self.assertIsNotNone(svg_text)
        self.assertIn("<svg", svg_text)

        # 4. Test start helper
        start_data = await fetcher.start(client=mock_client)
        self.assertIsNotNone(start_data)
        cookies, headers, s_url = start_data
        self.assertEqual(headers.get("X-CSRF-Token"), "mock_csrf_token_12345")
        self.assertIn("mock.svg", s_url)

        await mock_client.aclose()

    async def test_fetcher_page_with_prices(self):
        html_with_prices = """
        <!DOCTYPE html>
        <html>
        <head><meta name="csrf-token" content="token123"></head>
        <body>
        <script>
        var prices_of_event = '{"135001":{"id":"135001","price":"250.00"},"135002":{"id":"135002","price":"150.00"}}';
        </script>
        </body>
        </html>
        """
        def mock_handler(request: httpx.Request) -> httpx.Response:
            url_str = str(request.url)
            if "/kupit-bilet/47425/" in url_str or url_str.endswith("/47425/"):
                return httpx.Response(200, text=html_with_prices, headers={"set-cookie": "sess=1"})
            return httpx.Response(404)

        transport = httpx.MockTransport(mock_handler)
        fetcher = Fetcher(event_id="47425")
        mock_client = httpx.AsyncClient(base_url="https://www.ticketpro.by/kupit-bilet", transport=transport)

        page_res = await fetcher.fetch_page(event_id="47425", client=mock_client)
        self.assertIsNotNone(page_res)
        html, cookies, csrf = page_res
        self.assertEqual(csrf, "token123")

        prices = DefaultParser.extract_event_prices(html)
        self.assertEqual(len(prices), 2)
        self.assertIn("135001", prices)

        await mock_client.aclose()


if __name__ == "__main__":
    unittest.main()
