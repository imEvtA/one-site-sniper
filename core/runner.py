import asyncio
import logging
from typing import Any, Callable

from core.bot import BotSession, BotStatus
from core.pipeline import HuntingContext
from core.tasks.parser import BaseParser, DefaultParser
from core.tasks.utils.constants import EVENT_ID

logger = logging.getLogger("core.runner")


class Core:
    """
    Легковесный фасад-обертка для запуска снайпера в автономном CLI/скриптовом режиме.
    Делегирует исполнение классу BotSession.
    """
    def __init__(
        self,
        event_id: str = EVENT_ID,
        target_tickets: int = 1,
        num_consumers: int = 5,
        svg_url: str = "https://auth.ticketpro.by/ticket/file/temp/mock.svg",
        parser: BaseParser | None = None,
        event_callback: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        valid_prices = getattr(parser, "valid_price_ids", set()) if parser else set()
        allowed_sectors = getattr(parser, "allowed_sectors", None) if parser else None
        self.ctx = HuntingContext(
            event_id=event_id,
            event_name=f"Событие #{event_id}",
            svg_url=svg_url,
            target_tickets=target_tickets,
            num_consumers=num_consumers,
            poll_interval=1.0,
            cookies={},
            csrf_token=None,
            valid_price_ids=valid_prices,
            allowed_sectors=allowed_sectors,
        )
        self.session = BotSession(self.ctx, parser_class=type(parser) if parser else DefaultParser)
        self.event_callback = event_callback
        self.event_id = event_id
        self.target_tickets = target_tickets
        self.num_consumers = num_consumers
        self.parser = self.session.parser

    async def run(
        self,
        get_client: Any = None,
        post_client: Any = None,
        initial_cookies: dict[str, str] | None = None,
        initial_headers: dict[str, str] | None = None,
    ) -> int:
        if initial_cookies:
            self.session.ctx.cookies = initial_cookies

        if self.event_callback:
            q = self.session.subscribe()
            async def forwarder():
                try:
                    while True:
                        msg_str = await q.get()
                        if msg_str is None:
                            break
                        import json
                        data = json.loads(msg_str)
                        res = self.event_callback(data)
                        import inspect
                        if inspect.isawaitable(res):
                            await res
                except asyncio.CancelledError:
                    pass
                finally:
                    self.session.unsubscribe(q)

            f_task = asyncio.create_task(forwarder())
            try:
                return await self.session._hunt(get_client=get_client, post_client=post_client)
            finally:
                f_task.cancel()
        else:
            return await self.session._hunt(get_client=get_client, post_client=post_client)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    core = Core(event_id=EVENT_ID, target_tickets=1, num_consumers=3)
    asyncio.run(core.run())


if __name__ == "__main__":
    main()