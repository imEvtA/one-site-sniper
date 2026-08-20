import unittest
from unittest.mock import AsyncMock, patch

from core.pipeline import (
    PipelineContext,
    PreflightError,
    PreflightPipeline,
    build_default_preflight_pipeline,
    build_presession_pipeline,
    build_start_pipeline,
)
from core.pipeline.steps import (
    PageStatusStep,
    AntiBotGuardStep,
    AuthAndCsrfStep,
    SchemeResolutionStep,
    PriceFilterValidatorStep,
)


class TestPreflightPipeline(unittest.IsolatedAsyncioTestCase):

    async def test_page_status_step_success(self):
        step = PageStatusStep()
        ctx = PipelineContext(event_id="123", event_name="Test Event", page_status=200)
        await step.execute(ctx)  # should not raise

    async def test_page_status_step_404_raises(self):
        step = PageStatusStep()
        ctx = PipelineContext(event_id="123", event_name="Test Event", page_status=404)
        with self.assertRaises(PreflightError) as cm:
            await step.execute(ctx)
        self.assertEqual(cm.exception.code, "EVENT_NOT_FOUND")

    async def test_page_status_step_500_raises(self):
        step = PageStatusStep()
        ctx = PipelineContext(event_id="123", event_name="Test Event", page_status=500)
        with self.assertRaises(PreflightError) as cm:
            await step.execute(ctx)
        self.assertEqual(cm.exception.code, "UPSTREAM_SERVER_ERROR")

    async def test_price_filter_step_matching(self):
        step = PriceFilterValidatorStep()
        event_prices = [
            {"id": "101", "price": 50.0},
            {"id": "102", "price": 100.0},
            {"id": "103", "price": 150.0},
        ]
        ctx = PipelineContext(
            event_id="123",
            event_name="Test",
            all_event_prices=event_prices,
            min_price=60.0,
            max_price=120.0,
        )
        await step.execute(ctx)
        self.assertEqual(ctx.resolved_price_ids, {"102"})

    async def test_price_filter_step_no_match_raises(self):
        step = PriceFilterValidatorStep()
        event_prices = [
            {"id": "101", "price": 50.0},
            {"id": "102", "price": 100.0},
        ]
        ctx = PipelineContext(
            event_id="123",
            event_name="Test",
            all_event_prices=event_prices,
            min_price=200.0,
        )
        with self.assertRaises(PreflightError) as cm:
            await step.execute(ctx)
        self.assertEqual(cm.exception.code, "NO_MATCHING_PRICES")

    @patch("core.pipeline.steps.scheme_resolution.Fetcher.fetch_scheme_url", new_callable=AsyncMock)
    async def test_scheme_resolution_step_success(self, mock_fetch_scheme):
        mock_fetch_scheme.return_value = "https://auth.ticketpro.by/ticket/file/temp/mock.svg"
        step = SchemeResolutionStep()
        ctx = PipelineContext(event_id="123", event_name="Test")
        await step.execute(ctx)
        self.assertEqual(ctx.svg_url, "https://auth.ticketpro.by/ticket/file/temp/mock.svg")

    @patch("core.pipeline.steps.scheme_resolution.Fetcher.fetch_scheme_url", new_callable=AsyncMock)
    async def test_scheme_resolution_step_not_found_raises(self, mock_fetch_scheme):
        mock_fetch_scheme.return_value = None
        step = SchemeResolutionStep()
        ctx = PipelineContext(event_id="123", event_name="Test")
        with self.assertRaises(PreflightError) as cm:
            await step.execute(ctx)
        self.assertEqual(cm.exception.code, "SCHEME_NOT_FOUND")

    @patch("core.pipeline.steps.scheme_resolution.Fetcher.fetch_scheme_url", new_callable=AsyncMock)
    async def test_two_phase_pipeline_success(self, mock_fetch_scheme):
        mock_fetch_scheme.return_value = "https://auth.ticketpro.by/ticket/file/temp/mock.svg"

        # 1. Фаза пресессии (получение схемы и токена)
        presession_pipe = build_presession_pipeline()
        ctx_pre = PipelineContext(
            event_id="47425",
            event_name="Концерт",
            csrf_token="csrf_123",
            page_status=200,
            all_event_prices=[{"id": "1001", "price": 50.0}],
        )
        for step in presession_pipe.steps:
            await step.execute(ctx_pre)

        self.assertEqual(ctx_pre.svg_url, "https://auth.ticketpro.by/ticket/file/temp/mock.svg")

        # 2. Фаза старта (быстрая валидация цен без повторного запроса схемы)
        start_pipe = build_start_pipeline()
        ctx_start = PipelineContext(
            event_id=ctx_pre.event_id,
            event_name=ctx_pre.event_name,
            target_tickets=2,
            svg_url=ctx_pre.svg_url,
            all_event_prices=ctx_pre.all_event_prices,
            allowed_price_ids=["1001"],
            page_status=200,
        )
        hunting_ctx = await start_pipe.run(ctx_start)
        self.assertEqual(hunting_ctx.svg_url, "https://auth.ticketpro.by/ticket/file/temp/mock.svg")
        self.assertEqual(hunting_ctx.valid_price_ids, {"1001"})


if __name__ == "__main__":
    unittest.main()
