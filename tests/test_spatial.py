import unittest
from core.spatial import SpatialBox, BitSpatialEncoder, spatial_encoder


class TestBitSpatialEncoder(unittest.TestCase):
    def setUp(self):
        self.encoder = BitSpatialEncoder()

    def test_encode_decode_roundtrip(self):
        original = SpatialBox(
            location_id=1024,
            row_start=5,
            row_count=10,
            seat_start=12,
            seat_count=20,
            price_mask=0b0000000000000101,  # prices 0 and 2
        )
        packed = self.encoder.encode(original)
        decoded = self.encoder.decode(packed)
        self.assertEqual(original, decoded)

    def test_wildcard_all_zeros(self):
        # 0 = Any location, any row, any seat, any price
        wildcard_box = SpatialBox()
        packed = self.encoder.encode(wildcard_box)
        self.assertEqual(packed, 0)
        self.assertTrue(self.encoder.is_match(packed, location_id=1, row=50, seat=100, price_index=3))
        self.assertTrue(self.encoder.is_match(packed, location_id=9999, row=1, seat=1, price_index=0))

    def test_location_filter(self):
        # Filter strictly for location_id=42
        box = SpatialBox(location_id=42)
        packed = self.encoder.encode(box)

        self.assertTrue(self.encoder.is_match(packed, location_id=42, row=10, seat=20))
        self.assertFalse(self.encoder.is_match(packed, location_id=43, row=10, seat=20))

    def test_row_and_seat_range(self):
        # Rows 5..9 (row_start=5, row_count=5), Seats 10..13 (seat_start=10, seat_count=4)
        box = SpatialBox(
            location_id=1,
            row_start=5,
            row_count=5,
            seat_start=10,
            seat_count=4,
        )
        packed = self.encoder.encode(box)

        # In range
        self.assertTrue(self.encoder.is_match(packed, location_id=1, row=5, seat=10))
        self.assertTrue(self.encoder.is_match(packed, location_id=1, row=9, seat=13))
        self.assertTrue(self.encoder.is_match(packed, location_id=1, row=7, seat=12))

        # Out of range
        self.assertFalse(self.encoder.is_match(packed, location_id=1, row=4, seat=10))   # row before
        self.assertFalse(self.encoder.is_match(packed, location_id=1, row=10, seat=10))  # row after
        self.assertFalse(self.encoder.is_match(packed, location_id=1, row=5, seat=9))    # seat before
        self.assertFalse(self.encoder.is_match(packed, location_id=1, row=5, seat=14))   # seat after

    def test_price_mask(self):
        # Allow price index 1 (0b0010 = 2) and price index 3 (0b1000 = 8) -> mask = 10
        box = SpatialBox(price_mask=(1 << 1) | (1 << 3))
        packed = self.encoder.encode(box)

        self.assertTrue(self.encoder.is_match(packed, location_id=1, row=1, seat=1, price_index=1))
        self.assertTrue(self.encoder.is_match(packed, location_id=1, row=1, seat=1, price_index=3))
        self.assertFalse(self.encoder.is_match(packed, location_id=1, row=1, seat=1, price_index=0))
        self.assertFalse(self.encoder.is_match(packed, location_id=1, row=1, seat=1, price_index=2))
        self.assertFalse(self.encoder.is_match(packed, location_id=1, row=1, seat=1, price_index=16)) # out of 16-bit range

    def test_boundary_max_values(self):
        # Max 16-bit loc, max 8-bit coords, max 16-bit prices
        box = SpatialBox(
            location_id=65535,
            row_start=255,
            row_count=1,
            seat_start=255,
            seat_count=1,
            price_mask=65535,
        )
        packed = self.encoder.encode(box)
        decoded = self.encoder.decode(packed)
        self.assertEqual(box, decoded)
        self.assertTrue(self.encoder.is_match(packed, location_id=65535, row=255, seat=255, price_index=15))
        self.assertFalse(self.encoder.is_match(packed, location_id=65534, row=255, seat=255, price_index=15))


if __name__ == "__main__":
    unittest.main()
