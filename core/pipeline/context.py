from dataclasses import dataclass, field
from typing import Any
import httpx


@dataclass
class PipelineContext:
    """
    Входной и промежуточный контекст работы Preflight Pipeline.
    Чистый Python dataclass без внешних зависимостей от фреймворков.
    """
    event_id: str
    event_name: str
    target_tickets: int = 1
    num_consumers: int = 5
    poll_interval: float = 1.0
    raw_cookies: dict[str, str] = field(default_factory=dict)
    csrf_token: str | None = None
    page_status: int = 200

    # Пользовательские фильтры
    allowed_price_ids: list[str] | None = None
    min_price: float | None = None
    max_price: float | None = None
    allowed_sectors: list[str] | None = None

    # Обогащаемые результаты проверок
    svg_url: str | None = None
    all_event_prices: list[dict] = field(default_factory=list)
    resolved_price_ids: set[str] = field(default_factory=set)
    client: httpx.AsyncClient | None = None


@dataclass
class HuntingContext:
    """
    Финальный валидированный контекст, готовый для запуска BotSession.
    """
    event_id: str
    event_name: str
    svg_url: str
    target_tickets: int
    num_consumers: int
    poll_interval: float
    cookies: dict[str, str]
    csrf_token: str | None
    valid_price_ids: set[str]
    allowed_sectors: list[str] | None = None
    all_event_prices: list[dict] = field(default_factory=list)
