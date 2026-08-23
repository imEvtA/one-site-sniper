from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class SpatialBox:
    """
    Пространственный прямоугольный фильтр зоны зала и ценовой категории.
    Значение 0 для count / mask / location_id означает отсутствие ограничения (Wildcard / Any).
    """
    location_id: int = 0      # 16 бит: ID сектора / трибуны (0..65535, 0 = Any)
    row_start: int = 0        # 8 бит: начальный ряд (0..255)
    row_count: int = 0        # 8 бит: количество рядов (0..255, 0 = все ряды)
    seat_start: int = 0       # 8 бит: начальное место (0..255)
    seat_count: int = 0       # 8 бит: количество мест (0..255, 0 = все места)
    price_mask: int = 0       # 16 бит: битовая маска до 16 ценовых категорий (0 = все цены)


class BaseSpatialEncoder(ABC):
    """
    Абстрактный контракт кодирования, декодирования и наносекундного матчинга пространственных зон.
    """
    @abstractmethod
    def encode(self, box: SpatialBox) -> int:
        """Упаковывает SpatialBox в 64-битное целое число (uint64)."""
        pass

    @abstractmethod
    def decode(self, packed_val: int) -> SpatialBox:
        """Распаковывает 64-битное целое число обратно в SpatialBox."""
        pass

    @abstractmethod
    def is_match(
        self,
        packed_box: int,
        location_id: int,
        row: int,
        seat: int,
        price_index: int = 0,
    ) -> bool:
        """
        Сверхбыстрая проверка попадания билета в пространственный фильтр.
        """
        pass
