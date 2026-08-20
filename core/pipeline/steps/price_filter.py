from core.pipeline.base import PipelineStep
from core.pipeline.context import PipelineContext
from core.pipeline.exceptions import PreflightError


class PriceFilterValidatorStep(PipelineStep):
    name = "PriceFilterValidatorStep"

    async def execute(self, ctx: PipelineContext) -> None:
        """
        Сопоставляет выбранные пользователем цены с реальными категориями цен мероприятия.
        Формирует точный set валидных price_id для парсера.
        """
        # Если есть список доступных цен мероприятия
        event_prices = ctx.all_event_prices or []
        valid_ids: set[str] = set()

        if event_prices:
            for p in event_prices:
                pid = str(p.get("id", ""))
                try:
                    price_val = float(p.get("price", 0))
                except (ValueError, TypeError):
                    price_val = 0.0

                # 1. Фильтр по явным ID
                if ctx.allowed_price_ids and pid not in ctx.allowed_price_ids:
                    continue

                # 2. Фильтр по диапазону
                if ctx.min_price is not None and price_val < ctx.min_price:
                    continue
                if ctx.max_price is not None and price_val > ctx.max_price:
                    continue

                valid_ids.add(pid)

            # Если пользователь задал фильтры, но ни одна категория не подошла
            if (ctx.allowed_price_ids or ctx.min_price is not None or ctx.max_price is not None) and not valid_ids:
                raise PreflightError(
                    code="NO_MATCHING_PRICES",
                    message="Выбранный диапазон цен не соответствует ни одной доступной категории мероприятия.",
                    hint="Сбросьте фильтр цен или выберите другие ценовые категории в оверлее.",
                    step_name=self.name,
                )

        elif ctx.allowed_price_ids:
            valid_ids = set(str(x) for x in ctx.allowed_price_ids)

        ctx.resolved_price_ids = valid_ids
