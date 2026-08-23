import asyncio
import unittest

from core.spatial import spatial_encoder, SpatialBox
from core.tasks.payloads import FilterSnapshot
from core.tasks.parser import DefaultParser, Ticket
from core.tasks.base_fetcher import BaseFetcher
from core.tasks.base_consumer import BaseConsumer


class MockCustomFetcher(BaseFetcher):
    async def fetch_page(self, event_id=None, client=None):
        return ("<html>mock</html>", {"sess": "1"}, "csrf_tok")

    async def fetch_scheme_url(self, event_id=None, client=None):
        return "https://other-ticket-site.com/scheme.svg"

    async def fetch_svg(self, svg_url, client=None):
        return """
        <svg>
            <g price_id="10">
                <circle id="seat_1" name="1:A/2:5/3:2/4:8" />
                <circle id="seat_2" name="1:A/2:9/3:2/4:8" />
            </g>
        </svg>
        """


class TestSpatialParserIntegration(unittest.TestCase):
    def test_spatial_parser_filtering(self):
        # Box for sector (loc_id) 5, row 2, seat 8
        box_sec_5 = spatial_encoder.encode(SpatialBox(location_id=5, row_start=2, row_count=1, seat_start=8, seat_count=1))

        snapshot = FilterSnapshot(
            filter_boxes=(box_sec_5,),
            valid_price_ids=frozenset(["10"]),
        )

        parser = DefaultParser(snapshot=snapshot)

        svg = """
        <svg>
            <g price_id="10">
                <circle id="101" name="1:A/2:5/3:2/4:8" />
                <circle id="102" name="1:A/2:9/3:2/4:8" />
            </g>
        </svg>
        """

        tickets = parser.parse(svg)
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0].ticket_id, "101")

    def test_atomic_snapshot_swap(self):
        box_sec_9 = spatial_encoder.encode(SpatialBox(location_id=9))
        snapshot1 = FilterSnapshot(filter_boxes=(box_sec_9,))

        parser = DefaultParser()
        parser.set_snapshot(snapshot1)

        svg = """
        <svg>
            <g price_id="10">
                <circle id="101" name="1:A/2:5/3:2/4:8" />
                <circle id="102" name="1:A/2:9/3:2/4:8" />
            </g>
        </svg>
        """

        # Matches only sector 9
        self.assertEqual(len(parser.parse(svg)), 1)
        self.assertEqual(parser.parse(svg)[0].ticket_id, "102")

        # Swap to sector 5
        box_sec_5 = spatial_encoder.encode(SpatialBox(location_id=5))
        parser.set_snapshot(FilterSnapshot(filter_boxes=(box_sec_5,)))

        # Now matches only sector 5
        self.assertEqual(len(parser.parse(svg)), 1)
        self.assertEqual(parser.parse(svg)[0].ticket_id, "101")


if __name__ == "__main__":
    unittest.main()
