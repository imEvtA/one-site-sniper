import logging
import httpx
from core.pipeline.base import PipelineStep
from core.pipeline.context import PipelineContext
from core.pipeline.exceptions import PreflightError
from core.tasks.fetcher import Fetcher

logger = logging.getLogger("core.pipeline")


class AuthAndCsrfStep(PipelineStep):
    name = "AuthAndCsrfStep"

    async def execute(self, ctx: PipelineContext) -> None:
        """
        Проверяет наличие кук сессии и CSRF-токена.
        Если токена нет, делает фоновый запрос к странице мероприятия для его получения.
        """
        # Если CSRF токен уже передан из браузера или предсессии
        if ctx.csrf_token:
            return

        # Если токена нет, пробуем получить его через Fetcher
        fetcher = Fetcher()
        try:
            client = ctx.client or httpx.AsyncClient(timeout=15.0)
            res = await fetcher.fetch_page(ctx.event_id, client=client)
            if res and isinstance(res, tuple) and len(res) == 3:
                html_text, cookies, csrf_token = res
                if csrf_token:
                    ctx.csrf_token = csrf_token
                if cookies:
                    ctx.raw_cookies.update(cookies)
        except Exception as e:
            logger.warning(f"[AuthAndCsrfStep] Could not extract CSRF token for event {ctx.event_id}: {e}")
