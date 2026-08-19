import asyncio
import httpx
import re
import time
import logging

try:
    from .parser import BaseParser, DefaultParser, Ticket
    from .utils.constants import EVENT_ID, EVENT_URL, SCHEME_API_URL, HEADERS_TEMPLATE
except ImportError:
    from parser import BaseParser, DefaultParser, Ticket
    from utils.constants import EVENT_ID, EVENT_URL, SCHEME_API_URL, HEADERS_TEMPLATE


from typing import Any


class Fetcher:
    def __init__(
        self,
        event_id: str = EVENT_ID,
        parser: BaseParser | None = None,
        config: Any | None = None,
    ) -> None:
        self.event_id = event_id
        self.event_url = EVENT_URL
        self.scheme_api_url = SCHEME_API_URL
        self.headers_template = HEADERS_TEMPLATE.copy()
        self.headers_template["Referer"] = f"{self.event_url}/{self.event_id}/"
        self.parser = parser or DefaultParser()
        self.config = config
        self.prices: dict[str, dict[str, Any]] = {}

    async def update_cookies(
        self,
        client: httpx.AsyncClient | None = None
    ) -> tuple[dict[str, str], dict[str, str]] | None:
        """
        Обновляет сессионные куки и CSRF-токен события с главной страницы.
        Также извлекает актуальный каталог цен.
        """
        if client is not None:
            resp = await client.get(f"/{self.event_id}/")
        else:
            async with httpx.AsyncClient(base_url=self.event_url) as c:
                resp = await c.get(f"/{self.event_id}/")

        if resp.status_code != 200:
            return None

        cookies = dict(resp.cookies)
        headers = self.headers_template.copy()
        token_search = re.search(r'name="csrf-token" content="(.*?)"', resp.text)
        if token_search is None:
            return None
        headers["X-CSRF-Token"] = token_search.group(1)

        # Парсинг блока цен со страницы
        self.prices = self.parser.extract_event_prices(resp.text)
        if self.config is not None and hasattr(self.config, "update_valid_prices"):
            self.config.update_valid_prices(self.prices)

        return cookies, headers

    async def fetch_prices(
        self,
        client: httpx.AsyncClient | None = None
    ) -> dict[str, dict[str, Any]]:
        """
        Выполняет запрос страницы события, парсит блок цен с помощью инкапсулированного
        парсера и обновляет множество валидных price_id в связанном BotConfig.
        """
        own_client = False
        if client is None:
            client = httpx.AsyncClient(base_url=self.event_url)
            own_client = True

        try:
            resp = await client.get(f"/{self.event_id}/")
            if resp.status_code == 200:
                self.prices = self.parser.extract_event_prices(resp.text)
                if self.config is not None and hasattr(self.config, "update_valid_prices"):
                    self.config.update_valid_prices(self.prices)
                return self.prices
            return {}
        finally:
            if own_client:
                await client.aclose()


    async def start(
        self,
        client: httpx.AsyncClient | None = None
    ) -> tuple[dict[str, str], dict[str, str], str] | None:
        start_time = time.time()
        own_client = False
        if client is None:
            client = httpx.AsyncClient(base_url=self.event_url)
            own_client = True

        try:
            resp, scheme_resp = await asyncio.gather(
                client.get(f"/{self.event_id}/"),
                client.get(f"{self.scheme_api_url}/{self.event_id}")
            )

            if resp.status_code != 200 or scheme_resp.status_code != 200:
                return None      

            cookies = dict(resp.cookies)
            headers = self.headers_template.copy()
            token_search = re.search(r'name="csrf-token" content="(.*?)"', resp.text)
            if token_search is None:
                return None
            headers["X-CSRF-Token"] = token_search.group(1)

            svg_url = scheme_resp.json().get("path")
            if not svg_url:
                return None

            # Парсинг блока цен и запись в BotConfig valid_price_ids
            self.prices = self.parser.extract_event_prices(resp.text)
            if self.config is not None and hasattr(self.config, "update_valid_prices"):
                self.config.update_valid_prices(self.prices)

            end_time = time.time()
            logging.info(f"Time taken to fetch cookies, prices and SVG URL: {end_time - start_time:.4f}s")
            return cookies, headers, svg_url
        finally:
            if own_client:
                await client.aclose()

    async def get_svg_url(
        self,
        client: httpx.AsyncClient | None = None
    ) -> str | None:
        """
        Получает ссылку на временный SVG файл схемы зала.
        """
        if client is not None:
            resp = await client.get(f"{self.scheme_api_url}/{self.event_id}")
        else:
            async with httpx.AsyncClient() as c:
                resp = await c.get(f"{self.scheme_api_url}/{self.event_id}")

        if resp.status_code != 200:
            return None

        return resp.json().get("path")




    async def get_tickets(
        self,
        svg_url: str,
        client: httpx.AsyncClient | None = None,
        queue: asyncio.Queue | None = None
    ) -> list[Ticket]:
        start_time = time.time()

        if client is not None:
            svg_resp = await client.get(svg_url)
        else:
            async with httpx.AsyncClient() as c:
                svg_resp = await c.get(svg_url)

        if svg_resp.status_code != 200:
            return []

        end_time = time.time()
        logging.info(f"Time taken to fetch SVG: {end_time - start_time:.4f}s")
        return self.parser.parse(svg_resp.text, queue=queue)

    async def run(self) -> None:
        logging.info("Initializing session and fetching SVG URL...")
        init_data = await self.start()
        if not init_data:
            logging.error("Failed to initialize session")
            return None

        cookies, headers, svg_url = init_data
        logging.info(f"Obtained svg_url: {svg_url}")

        async with httpx.AsyncClient() as client:
            tickets = await self.get_tickets(svg_url, client=client)
            logging.info(f"Total tickets parsed: {len(tickets)}")
            if tickets:
                logging.info(f"First 5 tickets: {tickets[:5]}")


def main() -> None:
    asyncio.run(Fetcher().run())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()