from abc import ABC, abstractmethod
from typing import Any

import httpx


class BaseFetcher(ABC):
    """
    Абстрактный контракт сетевого I/O для получения данных о мероприятии и схемы зала.
    Изолирует ядро от конкретных URL и протоколов билетных систем.
    """

    @abstractmethod
    async def fetch_page(
        self,
        event_id: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> tuple[str, dict[str, str], str | None] | None:
        """Получает страницу мероприятия, HTML, куки и CSRF-токен."""
        pass

    @abstractmethod
    async def fetch_scheme_url(
        self,
        event_id: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> str | None:
        """Определяет прямой URL для загрузки интерактивной SVG-схемы."""
        pass

    @abstractmethod
    async def fetch_svg(
        self,
        svg_url: str,
        client: httpx.AsyncClient | None = None,
    ) -> str | None:
        """Загружает сырой XML/SVG текст схемы зала."""
        pass
