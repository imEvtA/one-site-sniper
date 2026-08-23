from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class BookedTicketPayload:
    """
    Неизменяемый результат успешного бронирования места консьюмером.
    Передается в AllocationManager сервисного слоя.
    """
    event_id: str
    ticket_id: str
    price_id: str
    price_value: float
    seat_info: str
    session_cookies: dict[str, str]
    booked_at: float
    expires_at: float
    location_id: int = 0
    row: int = 0
    seat: int = 0
    price_index: int = 0
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class BookingItem:
    """
    Атомарный DTO брони для отдачи в клиентские интерфейсы (Web HUD, TG, REST).
    """
    booking_id: str
    ticket_id: str
    seat_info: str
    price: float
    expires_at: float
    time_left_sec: int
    cookies: dict[str, str]


@dataclass(slots=True, frozen=True)
class FilterSnapshot:
    """
    Иммутабельный снимок фильтров активного спроса для Zero-Lock подмены в BotSession.
    """
    filter_boxes: tuple[int, ...] = field(default_factory=tuple)  # Кортеж 64-битных packed int
    valid_price_ids: frozenset[str] = field(default_factory=frozenset)
    allowed_sectors: frozenset[str] = field(default_factory=frozenset)
