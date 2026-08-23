import asyncio
import logging
from typing import Any

import httpx

from core.tasks.fetcher import Fetcher
from core.tasks.parser import BaseParser, DefaultParser, Ticket

logger = logging.getLogger("core.producer")


class ProducerUnit:
    """
    Автономная единица мониторинга схемы зала (1 Fetcher + 1 Parser).
    Инкапсулирует загрузку схемы и извлечение свободных билетов.
    """

    def __init__(
        self,
        event_id: str,
        parser: BaseParser | None = None,
        fetcher: Fetcher | None = None,
    ) -> None:
        self.event_id = str(event_id)
        self.fetcher = fetcher or Fetcher(event_id=self.event_id)
        self.parser = parser or DefaultParser()

    async def poll_once(
        self,
        svg_url: str,
        client: httpx.AsyncClient | None = None,
    ) -> list[Ticket]:
        """
        Выполняет один цикл загрузки SVG схемы и извлечения свободных билетов.
        """
        svg_text = await self.fetcher.fetch_svg(svg_url, client=client)
        if not svg_text:
            return []

        return self.parser.parse(svg_text)
