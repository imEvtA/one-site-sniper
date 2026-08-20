import httpx
from core.pipeline.base import PipelineStep
from core.pipeline.context import PipelineContext
from core.pipeline.exceptions import PreflightError
from core.tasks.fetcher import Fetcher


class SchemeResolutionStep(PipelineStep):
    name = "SchemeResolutionStep"

    async def execute(self, ctx: PipelineContext) -> None:
        """
        Запрашивает API Ticketpro и получает URL SVG-схемы зала.
        Если мероприятие без мест на схеме (входной билет), генерирует понятную ошибку.
        """
        fetcher = Fetcher()
        try:
            client = ctx.client or httpx.AsyncClient(timeout=15.0)
            svg_url = await fetcher.fetch_scheme_url(ctx.event_id, client=client)
            if not svg_url:
                raise PreflightError(
                    code="SCHEME_NOT_FOUND",
                    message="Интерактивная схема зала не найдена.",
                    hint="Для этого мероприятия нет рассадки по местам (свободный вход или танцпол). Выберите билеты вручную.",
                    step_name=self.name,
                )
            ctx.svg_url = svg_url
        except PreflightError:
            raise
        except Exception as e:
            raise PreflightError(
                code="SCHEME_API_ERROR",
                message=f"Ошибка при запросе схемы зала: {e}",
                hint="Проверьте подключение к сети и повторите попытку.",
                step_name=self.name,
            )
