from core.spatial.base import BaseSpatialEncoder, SpatialBox

# Сдвиги битовых полей в 64-битном числе (Little-Endian порядок):
# [location_id: 16] [row_start: 8] [row_count: 8] [seat_start: 8] [seat_count: 8] [price_mask: 16]
LOCATION_SHIFT = 48
ROW_START_SHIFT = 40
ROW_COUNT_SHIFT = 32
SEAT_START_SHIFT = 24
SEAT_COUNT_SHIFT = 16
PRICE_MASK_SHIFT = 0

LOCATION_MASK = 0xFFFF
BYTE_MASK = 0xFF
PRICE_MASK = 0xFFFF
UINT64_MAX = 0xFFFFFFFFFFFFFFFF


class BitSpatialEncoder(BaseSpatialEncoder):
    """
    Универсальный 64-битный пространственный энкодер/матчер.
    Выполняет упаковку и проверку за 1 такт процессора без создания объектов в памяти.
    """

    def encode(self, box: SpatialBox) -> int:
        loc = box.location_id & LOCATION_MASK
        r_start = box.row_start & BYTE_MASK
        r_count = box.row_count & BYTE_MASK
        s_start = box.seat_start & BYTE_MASK
        s_count = box.seat_count & BYTE_MASK
        p_mask = box.price_mask & PRICE_MASK

        return (
            (loc << LOCATION_SHIFT)
            | (r_start << ROW_START_SHIFT)
            | (r_count << ROW_COUNT_SHIFT)
            | (s_start << SEAT_START_SHIFT)
            | (s_count << SEAT_COUNT_SHIFT)
            | (p_mask << PRICE_MASK_SHIFT)
        ) & UINT64_MAX

    def decode(self, packed_val: int) -> SpatialBox:
        loc = (packed_val >> LOCATION_SHIFT) & LOCATION_MASK
        r_start = (packed_val >> ROW_START_SHIFT) & BYTE_MASK
        r_count = (packed_val >> ROW_COUNT_SHIFT) & BYTE_MASK
        s_start = (packed_val >> SEAT_START_SHIFT) & BYTE_MASK
        s_count = (packed_val >> SEAT_COUNT_SHIFT) & BYTE_MASK
        p_mask = (packed_val >> PRICE_MASK_SHIFT) & PRICE_MASK

        return SpatialBox(
            location_id=loc,
            row_start=r_start,
            row_count=r_count,
            seat_start=s_start,
            seat_count=s_count,
            price_mask=p_mask,
        )

    def is_match(
        self,
        packed_box: int,
        location_id: int,
        row: int,
        seat: int,
        price_index: int = 0,
    ) -> bool:
        # 1. Локация (0 = Any)
        loc = (packed_box >> LOCATION_SHIFT) & LOCATION_MASK
        if loc != 0 and loc != location_id:
            return False

        # 2. Ряд (row_count == 0 -> All rows)
        r_count = (packed_box >> ROW_COUNT_SHIFT) & BYTE_MASK
        if r_count != 0:
            r_start = (packed_box >> ROW_START_SHIFT) & BYTE_MASK
            if not (0 <= (row - r_start) < r_count):
                return False

        # 3. Место (seat_count == 0 -> All seats)
        s_count = (packed_box >> SEAT_COUNT_SHIFT) & BYTE_MASK
        if s_count != 0:
            s_start = (packed_box >> SEAT_START_SHIFT) & BYTE_MASK
            if not (0 <= (seat - s_start) < s_count):
                return False

        # 4. Цена (price_mask == 0 -> All prices)
        p_mask = (packed_box >> PRICE_MASK_SHIFT) & PRICE_MASK
        if p_mask != 0:
            if price_index < 0 or price_index >= 16:
                return False
            if ((p_mask >> price_index) & 1) == 0:
                return False

        return True


# Глобальный синглтон энкодера для быстрого доступа
spatial_encoder = BitSpatialEncoder()
