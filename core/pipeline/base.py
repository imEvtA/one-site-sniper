import logging
from abc import ABC, abstractmethod

from core.pipeline.context import PipelineContext, HuntingContext

logger = logging.getLogger("core.pipeline")


class PipelineStep(ABC):
    """
    Абстрактный шаг конвейера проверок.
    """
    name: str = "BaseStep"

    @abstractmethod
    async def execute(self, ctx: PipelineContext) -> None:
        """
        Выполняет проверку или обогащает контекст.
        При ошибке выбрасывает PreflightError.
        """
        pass


class PreflightPipeline:
    """
    Конвейер preflight-шагов с поддержкой динамического добавления и вставки.
    """
    def __init__(self, steps: list[PipelineStep] | None = None):
        self.steps: list[PipelineStep] = steps or []

    def add_step(self, step: PipelineStep) -> "PreflightPipeline":
        self.steps.append(step)
        return self

    def insert_before(self, target_step_name: str, step: PipelineStep) -> "PreflightPipeline":
        idx = next((i for i, s in enumerate(self.steps) if s.name == target_step_name), -1)
        if idx != -1:
            self.steps.insert(idx, step)
        else:
            self.steps.append(step) # TODO: think about error here?
        return self

    async def run(self, ctx: PipelineContext) -> HuntingContext:
        """
        Выполняет все шаги конвейера по очереди и возвращает готовый HuntingContext.
        """
        for step in self.steps:
            logger.info(f"[PreflightPipeline] Executing step '{step.name}' for event {ctx.event_id}")
            await step.execute(ctx)

        if not ctx.svg_url:
            raise ValueError(f"Pipeline completed without resolving svg_url for event {ctx.event_id}")

        return HuntingContext(
            event_id=ctx.event_id,
            event_name=ctx.event_name,
            svg_url=ctx.svg_url,
            target_tickets=ctx.target_tickets,
            num_consumers=ctx.num_consumers,
            poll_interval=ctx.poll_interval,
            cookies=ctx.raw_cookies,
            csrf_token=ctx.csrf_token,
            valid_price_ids=ctx.resolved_price_ids,
            allowed_sectors=ctx.allowed_sectors,
            all_event_prices=ctx.all_event_prices,
        )
