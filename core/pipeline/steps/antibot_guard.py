from core.pipeline.base import PipelineStep
from core.pipeline.context import PipelineContext
from core.pipeline.exceptions import PreflightError


class AntiBotGuardStep(PipelineStep):
    name = "AntiBotGuardStep"

    async def execute(self, ctx: PipelineContext) -> None:
        """
        Проверяет отсутствие активных блокировок Cloudflare, DDoS-Guard или капчи.
        Точка расширения для подключения автоматического решения капчи или ротации прокси.
        """
        # Проверка ключевых кук или признаков блокировки
        cookies = ctx.raw_cookies
        if "cf_clearance" in cookies and not cookies["cf_clearance"]:
            raise PreflightError(
                code="CLOUDFLARE_CHALLENGE",
                message="Требуется прохождение проверки Cloudflare.",
                hint="Откройте сайт в браузере, пройдите проверку (Cloudflare Turnstile/Captcha) и перезапустите снайпер.",
                step_name=self.name,
            )
