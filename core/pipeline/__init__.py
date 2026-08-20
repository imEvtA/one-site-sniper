from core.pipeline.context import PipelineContext, HuntingContext
from core.pipeline.exceptions import PreflightError
from core.pipeline.base import PipelineStep, PreflightPipeline
from core.pipeline.steps import (
    PageStatusStep,
    AntiBotGuardStep,
    AuthAndCsrfStep,
    SchemeResolutionStep,
    PriceFilterValidatorStep,
)


def build_presession_pipeline() -> PreflightPipeline:
    """
    Фаза 1: Пассивный конвейер при заходе на страницу мероприятия.
    Проверяет статус, защиту, сессию и заранее получает URL SVG-схемы зала.
    """
    return PreflightPipeline([
        PageStatusStep(),
        AntiBotGuardStep(),
        AuthAndCsrfStep(),
        SchemeResolutionStep(),
    ])


def build_start_pipeline() -> PreflightPipeline:
    """
    Фаза 2: Активный конвейер при нажатии кнопки 'Старт снайпера'.
    Валидирует выбранные фильтры цен (и схему, если она не была получена ранее).
    """
    return PreflightPipeline([
        PageStatusStep(),
        PriceFilterValidatorStep(),
    ])


def build_default_preflight_pipeline() -> PreflightPipeline:
    """
    Полный конвейер (все шаги) для прямого/скриптового режима.
    """
    return PreflightPipeline([
        PageStatusStep(),
        AntiBotGuardStep(),
        AuthAndCsrfStep(),
        SchemeResolutionStep(),
        PriceFilterValidatorStep(),
    ])


__all__ = [
    "PipelineContext",
    "HuntingContext",
    "PreflightError",
    "PipelineStep",
    "PreflightPipeline",
    "build_presession_pipeline",
    "build_start_pipeline",
    "build_default_preflight_pipeline",
]
