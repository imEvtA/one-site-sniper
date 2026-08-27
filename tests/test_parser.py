import unittest
import asyncio
import queue
from core.tasks.parser import Parser, DefaultParser, CurrentTicketproParser, Ticket, BaseParser

MOCK_SVG = """
<svg xmlns="http://www.w3.org/2000/svg">
  <!-- Booked / Gray group without price_id -->
  <g stroke-width="1.0" stroke="#999999" fill="#999999">
    <circle id="99990001" cx="100.0" cy="200.0" r="5.0" prototype_id="111" />
    <circle id="99990002" cx="110.0" cy="200.0" r="5.0" prototype_id="112" />
  </g>
  <!-- Available Price Group 1 with modern attributes order -->
  <g stroke-width="1.0" stroke="#999999" fill="#0000FF" price_id="135001">
    <circle id="82200001" cx="1995.99" cy="1229.4" r="5.0" name="1:A/2:Sector1/3:1/4:10" prototype_id="60344425"/>
    <circle cx="1436.44" cy="2241.32" id="82200002" r="5.0" name="1:A/2:Sector2/3:2/4:20" prototype_id="60348705"/>
  </g>
  <!-- Available Price Group 2 -->
  <g stroke-width="1.0" stroke="#999999" fill="#008000" price_id="135002">
    <circle r="5.0" name="1:B/2:VIP/3:1/4:1" id="82200003" cx="500.0" cy="600.0" prototype_id="60344771"/>
    <circle id="82200004" name="1:B/2:Parter/3:5/4:15" cx="510.0" cy="600.0" r="5.0" prototype_id="60344772"/>
  </g>
</svg>
"""


class TestParser(unittest.TestCase):
    def test_parser_alias(self):
        self.assertIs(Parser, CurrentTicketproParser)
        self.assertTrue(issubclass(DefaultParser, BaseParser))
        self.assertTrue(issubclass(CurrentTicketproParser, BaseParser))

    def test_ticket_unpacking_and_properties(self):
        ticket = Ticket(ticket_id="12345", price_id="100", name="1:TribuneA/2:SectorVIP/3:Row5/4:Seat12")
        t_id, p_id = ticket
        self.assertEqual(t_id, "12345")
        self.assertEqual(p_id, "100")
        self.assertEqual(ticket.sector, "SectorVIP")
        self.assertEqual(ticket.row, "Row5")
        self.assertEqual(ticket.seat, "Seat12")

    def test_parse_excludes_gray_and_extracts_available(self):
        parser = CurrentTicketproParser()
        tickets = parser.parse(MOCK_SVG)
        self.assertEqual(len(tickets), 4)
        ticket_ids = [t.ticket_id for t in tickets]
        self.assertNotIn("99990001", ticket_ids)
        self.assertNotIn("99990002", ticket_ids)
        self.assertIn("82200001", ticket_ids)
        self.assertIn("82200002", ticket_ids)
        self.assertIn("82200003", ticket_ids)
        self.assertIn("82200004", ticket_ids)

    def test_allowed_price_ids_filter(self):
        parser = CurrentTicketproParser(allowed_price_ids=["135002"])
        tickets = parser.parse(MOCK_SVG)
        self.assertEqual(len(tickets), 2)
        for t in tickets:
            self.assertEqual(t.price_id, "135002")

    def test_allowed_sectors_filter(self):
        parser = CurrentTicketproParser(allowed_sectors=["VIP", "Parter"])
        tickets = parser.parse(MOCK_SVG)
        self.assertEqual(len(tickets), 2)
        sectors = [t.sector for t in tickets]
        self.assertListEqual(sectors, ["VIP", "Parter"])

    def test_custom_filter_fn(self):
        # Only seats with seat number == "10"
        parser = CurrentTicketproParser(filter_fn=lambda t: t.seat == "10")
        tickets = parser.parse(MOCK_SVG)
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0].ticket_id, "82200001")

    def test_price_range_filtering(self):
        event_prices = {
            "135001": {"id": "135001", "price": 286.0},
            "135002": {"id": "135002", "price": 186.0},
        }
        # Filter tickets with price <= 200
        parser = CurrentTicketproParser(event_prices=event_prices, max_price=200.0)
        tickets = parser.parse(MOCK_SVG)
        self.assertEqual(len(tickets), 2)
        for t in tickets:
            self.assertEqual(t.price_id, "135002")

    def test_parse_with_asyncio_queue(self):
        async def run_async():
            q = asyncio.Queue()
            parser = CurrentTicketproParser()
            parser.parse(MOCK_SVG, queue=q)
            self.assertEqual(q.qsize(), 4)
            item = await q.get()
            self.assertIsInstance(item, Ticket)
        asyncio.run(run_async())

    def test_extract_event_prices(self):
        html_with_let = """
        <script>
        let prices_of_event = '{"135001":{"id":"135001","color":"#FFFF80","price":"286.00"},"135002":{"id":"135002","color":"#804000","price":"186.50"}}';
        </script>
        """
        prices = CurrentTicketproParser.extract_event_prices(html_with_let)
        self.assertEqual(len(prices), 2)
        self.assertIn("135001", prices)
        self.assertEqual(prices["135001"]["price"], 286.0)
        self.assertEqual(prices["135001"]["color"], "#FFFF80")
        self.assertEqual(prices["135002"]["price"], 186.5)

        html_with_input = """
        <input name="prices_of_event" value="{&quot;135003&quot;:{&quot;id&quot;:&quot;135003&quot;,&quot;price&quot;:&quot;120.00&quot;}}">
        """
        prices_input = CurrentTicketproParser.extract_event_prices(html_with_input)
        self.assertEqual(len(prices_input), 1)
        self.assertEqual(prices_input["135003"]["price"], 120.0)

    def test_valid_price_ids_checking(self):
        valid_set = {"135001"}
        parser = CurrentTicketproParser(valid_price_ids=valid_set)
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
