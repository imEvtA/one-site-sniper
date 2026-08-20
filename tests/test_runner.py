import unittest
import asyncio
import httpx
from core.runner import Core

MOCK_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta name="csrf-token" content="test_token">
</head>
<body></body>
</html>
"""

MOCK_SVG = """
<svg xmlns="http://www.w3.org/2000/svg">
  <g stroke-width="1.0" fill="#0000FF" price_id="135001">
    <circle id="1001" name="1:A/2:Sector1/3:1/4:1" />
    <circle id="1002" name="1:A/2:Sector1/3:1/4:2" />
    <circle id="1003" name="1:A/2:Sector1/3:1/4:3" />
  </g>
</svg>
"""


class TestRunner(unittest.IsolatedAsyncioTestCase):
    async def test_core_end_to_end_flow(self):
        booked_tickets = []

        def mock_handler(request: httpx.Request) -> httpx.Response:
            url_str = str(request.url)
            if "/kupit-bilet" in url_str:
                return httpx.Response(200, text=MOCK_HTML, headers={"set-cookie": "sess=123"})
            elif "get-scheme-prices-grouped" in url_str:
                return httpx.Response(200, json={"file": "temp/mock.svg"})
            elif "mock.svg" in url_str:
                return httpx.Response(200, text=MOCK_SVG)
            elif "/api/ticket/ticket-reserve/" in url_str:
                ticket_id = request.url.params.get("ticket_id")
                booked_tickets.append(ticket_id)
                return httpx.Response(200, json={"status": "ok", "error": None})

            return httpx.Response(404)

        transport = httpx.MockTransport(mock_handler)
        get_client = httpx.AsyncClient(base_url="https://www.ticketpro.by/kupit-bilet", transport=transport)
        post_client = httpx.AsyncClient(transport=transport)

        core = Core(event_id="47425", target_tickets=2, num_consumers=3)

        # Run core with mock clients
        booked_count = await core.run(get_client=get_client, post_client=post_client)

        self.assertEqual(booked_count, 2)
        self.assertEqual(len(booked_tickets), 2)
        self.assertIn("1001", booked_tickets)
        self.assertIn("1002", booked_tickets)

        await get_client.aclose()
        await post_client.aclose()


if __name__ == "__main__":
    unittest.main()

