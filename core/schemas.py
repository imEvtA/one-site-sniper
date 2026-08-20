from typing import Any
from pydantic import BaseModel, Field, ConfigDict
from core.pipeline.context import PipelineContext


class StartBotRequest(BaseModel):
    """
    Входной DTO-запрос на запуск снайпера.
    """
    model_config = ConfigDict(extra="ignore")

    event_id: str
    event_name: str | None = None
    target_tickets: int = Field(default=1, ge=1, le=10)
    num_consumers: int = Field(default=5, ge=1, le=20)
    poll_interval: float = Field(default=1.0, ge=0.1)
    allowed_price_ids: list[str] | None = None
    min_price: float | None = None
    max_price: float | None = None
    allowed_sectors: list[str] | None = None
    csrf_token: str | None = None
    page_status: int | None = 200

    def to_pipeline_context(
        self,
        cookies: dict[str, str],
        all_event_prices: list[dict] | None = None,
        svg_url: str | None = None,
    ) -> PipelineContext:
        """
        Конвертирует DTO в чистый PipelineContext для ядра.
        """
        return PipelineContext(
            event_id=self.event_id,
            event_name=self.event_name or f"Событие #{self.event_id}",
            target_tickets=self.target_tickets,
            num_consumers=self.num_consumers,
            poll_interval=self.poll_interval,
            raw_cookies=cookies.copy() if cookies else {},
            csrf_token=self.csrf_token,
            page_status=self.page_status or 200,
            allowed_price_ids=self.allowed_price_ids,
            min_price=self.min_price,
            max_price=self.max_price,
            allowed_sectors=self.allowed_sectors,
            all_event_prices=all_event_prices or [],
            svg_url=svg_url,
        )


class StopBotRequest(BaseModel):
    event_id: str


class ActivateSessionRequest(BaseModel):
    event_id: str


class PreflightErrorDetail(BaseModel):
    code: str
    message: str
    hint: str
    step: str


class PresessionResponse(BaseModel):
    status: str = "ok"
    event_id: str
    event_name: str
    prices: list[dict[str, Any]]
    valid_price_ids: list[str]
    has_csrf: bool
    page_status: int = 200
    has_scheme: bool = True
    svg_url: str | None = None
    error: PreflightErrorDetail | None = None


class BotStatusResponse(BaseModel):
    status: str
    target: int
    booked: int
    event_id: str
    event_name: str
    is_running: bool
    started_at: str | None = None
    time_live: str | None = None
    valid_prices: list[str] = Field(default_factory=list)


class PreflightErrorResponse(BaseModel):
    status: str = "error"
    error: PreflightErrorDetail
