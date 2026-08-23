from abc import ABC, abstractmethod
from typing import Any

import httpx


class BaseConsumer(ABC):
    """
    Абстрактный контракт сетевого резервирования билета.
    Изолирует ядро от конкретных POST-эндпоинтов и форматов резервации.
    """

    @abstractmethod
    async def book(
        self,
        params: dict[str, Any],
        client: httpx.AsyncClient | None = None,
        cookies: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Выполняет сетевой запрос на бронирование места.
        Возвращает десериализованный JSON-ответ или словарь с ошибкой.
        """
        pass
