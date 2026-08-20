import unittest
import asyncio
import queue
from core.tasks.parser import Parser, DefaultParser, Ticket, BaseParser

MOCK_SVG = """
<svg xmlns="http://www.w3.org/2000/svg">
  <!-- Booked / Gray group -->
  <g stroke="#999999" fill="#999999">
    <circle id="99990001" name="1:A/2:Sector1/3:1/4:1" />
    <circle id="99990002" name="1:A/2:Sector1/3:1/4:2" />
  </g>
  <!-- Available Price Group 1 -->
  <g stroke-width="1.0" fill="#0000FF" price_id="135001">
    <circle id="82200001" name="1:A/2:Sector1/3:1/4:10" />
    <circle id="82200002" name="1:A/2:Sector2/3:2/4:20" />
  </g>
  <!-- Available Price Group 2 -->
  <g stroke-width="1.0" fill="#008000" price_id="135002">
    <circle id="82200003" name="1:B/2:VIP/3:1/4:1" />
    <circle id="82200004" name="1:B/2:Parter/3:5/4:15" />
  </g>
</svg>
"""


class TestParser(unittest.TestCase):
    def test_ticket_unpacking_and_properties(self):
        ticket = Ticket(ticket_id="12345", price_id="100", name="1:TribuneA/2:SectorVIP/3:Row5/4:Seat12")
        t_id, p_id = ticket
        self.assertEqual(t_id, "12345")
        self.assertEqual(p_id, "100")
        self.assertEqual(ticket.sector, "SectorVIP")
        self.assertEqual(ticket.row, "Row5")
        self.assertEqual(ticket.seat, "Seat12")

    def test_parse_excludes_gray_and_extracts_available(self):
        parser = DefaultParser()
        tickets = parser.parse(MOCK_SVG)
        self.assertEqual(len(tickets), 4)
        ticket_ids = [t.ticket_id for t in tickets]
        self.assertNotIn("99990001", ticket_ids)
        self.assertNotIn("99990002", ticket_ids)
        self.assertIn("82200001", ticket_ids)
        self.assertIn("82200003", ticket_ids)

    def test_allowed_price_ids_filter(self):
        parser = DefaultParser(allowed_price_ids=["135002"])
        tickets = parser.parse(MOCK_SVG)
        self.assertEqual(len(tickets), 2)
        for t in tickets:
            self.assertEqual(t.price_id, "135002")

    def test_allowed_sectors_filter(self):
        parser = DefaultParser(allowed_sectors=["VIP", "Parter"])
        tickets = parser.parse(MOCK_SVG)
        self.assertEqual(len(tickets), 2)
        sectors = [t.sector for t in tickets]
        self.assertListEqual(sectors, ["VIP", "Parter"])

    def test_custom_filter_fn(self):
        # Only seats with seat number == "10"
        parser = DefaultParser(filter_fn=lambda t: t.seat == "10")
        tickets = parser.parse(MOCK_SVG)
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0].ticket_id, "82200001")

    def test_parse_with_asyncio_queue(self):
        async def run_async():
            q = asyncio.Queue()
            parser = DefaultParser()
            parser.parse(MOCK_SVG, queue=q)
            self.assertEqual(q.qsize(), 4)
            item = await q.get()
    def test_extract_event_prices(self):
        html_with_var = """
        <script>
        var prices_of_event = '{"135001":{"id":"135001","color":"#FFFF80","price":"286.00"},"135002":{"id":"135002","color":"#804000","price":"186.50"}}';
        </script>
        """
        prices = DefaultParser.extract_event_prices(html_with_var)
        self.assertEqual(len(prices), 2)
        self.assertIn("135001", prices)
        self.assertEqual(prices["135001"]["price"], 286.0)
        self.assertEqual(prices["135001"]["color"], "#FFFF80")
        self.assertEqual(prices["135002"]["price"], 186.5)

        html_with_input = """
        <input name="prices_of_event" value="{&quot;135003&quot;:{&quot;id&quot;:&quot;135003&quot;,&quot;price&quot;:&quot;120.00&quot;}}">
        """
        prices_input = DefaultParser.extract_event_prices(html_with_input)
        self.assertEqual(len(prices_input), 1)
        self.assertEqual(prices_input["135003"]["price"], 120.0)

    def test_valid_price_ids_checking(self):
        valid_set = {"135001"}
        parser = DefaultParser(valid_price_ids=valid_set)
        tickets = parser.parse(MOCK_SVG)
        self.assertEqual(len(tickets), 2)
        for t in tickets:
            self.assertEqual(t.price_id, "135001")

        # Mutating shared set dynamically reflects in parser immediately
        valid_set.add("135002")
        tickets_all = parser.parse(MOCK_SVG)
        self.assertEqual(len(tickets_all), 4)


if __name__ == "__main__":
    unittest.main()

