from core.pipeline.base import PipelineStep
from core.pipeline.context import PipelineContext
from core.pipeline.exceptions import PreflightError


class PageStatusStep(PipelineStep):
    name = "PageStatusStep"

    async def execute(self, ctx: PipelineContext) -> None:
        status = ctx.page_status
        if status == 404:
            raise PreflightError(
                code="EVENT_NOT_FOUND",
                message=f"Страница мероприятия #{ctx.event_id} вернула статус 404 Not Found.",
                hint="Проверьте правильность ID мероприятия или вернитесь на афишу Ticketpro.",
                step_name=self.name,
            )
        elif status in (403, 429):
            raise PreflightError(
                code="ACCESS_RESTRICTED",
                message=f"Сервер Ticketpro временно ограничил доступ (HTTP {status}).",
                hint="Подождите несколько минут или перезагрузите страницу в браузере.",
                step_name=self.name,
            )
        elif status >= 500:
            raise PreflightError(
                code="UPSTREAM_SERVER_ERROR",
                message=f"Сервер Ticketpro вернул ошибку {status}.",
                hint="На стороне билетного оператора сбой. Повторите попытку позже.",
                step_name=self.name,
            )
        elif not (200 <= status < 300):
            raise PreflightError(
                code="INVALID_PAGE_STATUS",
                message=f"Недопустимый статус страницы мероприятия: HTTP {status}.",
                hint="Обновите страницу мероприятия (F5) и повторите запуск.",
                step_name=self.name,
            )
