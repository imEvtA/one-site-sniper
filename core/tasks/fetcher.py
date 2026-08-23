import asyncio
import logging
import re
import time
from typing import Any

import httpx

from .base_fetcher import BaseFetcher
from .utils.constants import EVENT_ID, EVENT_URL, SCHEME_API_URL, HEADERS_TEMPLATE, AUTH_SVG_BASE_URL

logger = logging.getLogger("core.fetcher")


class Fetcher(BaseFetcher):
    """
    Чистый сетевой клиент для Ticketpro (I/O слой).
    Отвечает исключительно за HTTP-запросы без привязки к парсерам или состоянию бота.
    """
    def __init__(
        self,
        event_id: str = EVENT_ID,
        event_url: str = EVENT_URL,
        scheme_api_url: str = SCHEME_API_URL,
        headers_template: dict[str, str] | None = None,
    ) -> None:
        self.event_id = event_id
        self.event_url = event_url
        self.scheme_api_url = scheme_api_url
        self.headers_template = (headers_template or HEADERS_TEMPLATE).copy()
        self.headers_template["Referer"] = f"{self.event_url}/{self.event_id}/"

    async def fetch_page(
        self,
        event_id: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> tuple[str, dict[str, str], str | None] | None:
        """
        Загружает HTML-страницу события.
        Возвращает (html_text, cookies_dict, csrf_token).
        """
        eid = event_id or self.event_id
        own_client = False
        if client is None:
            client = httpx.AsyncClient(base_url=self.event_url)
            own_client = True

        try:
            resp = await client.get(f"/{eid}/")
            if resp.status_code != 200:
                return None

            html_text = resp.text
            cookies = dict(resp.cookies)
            token_match = re.search(r'name="csrf-token" content="(.*?)"', html_text)
            csrf_token = token_match.group(1) if token_match else None

            return html_text, cookies, csrf_token
        except Exception as e:
            logger.warning(f"[Fetcher] Error fetching page for event {eid}: {e}")
            return None
        finally:
            if own_client:
                await client.aclose()

    async def fetch_scheme_url(
        self,
        event_id: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> str | None:
        """
        Запрашивает API схемы зала и возвращает прямой URL к SVG файлу.
        """
        eid = event_id or self.event_id
        own_client = False
        if client is None:
            client = httpx.AsyncClient()
            own_client = True

        try:
            resp = await client.get(f"{self.scheme_api_url}/{eid}")
            if resp.status_code != 200:
                logger.warning(f"[Fetcher] Scheme API returned HTTP {resp.status_code} for event {eid}")
                return None

            data = resp.json()
            file_path = data.get("path") or data.get("file")
            if not file_path:
                logger.warning(f"[Fetcher] No 'path' or 'file' key in scheme response for event {eid}: {data}")
                return None

            if file_path.startswith("http"):
                full_url = file_path
            else:
                full_url = f"{AUTH_SVG_BASE_URL}/{file_path.lstrip('/')}"

            logger.info(f"[Fetcher] Resolved SVG scheme URL for event {eid}: {full_url}")
            return full_url

        except Exception as e:
            logger.warning(f"[Fetcher] Error fetching scheme URL for event {eid}: {e}")
            return None
        finally:
            if own_client:
                await client.aclose()


    async def fetch_svg(
        self,
        svg_url: str,
        client: httpx.AsyncClient | None = None,
    ) -> str | None:
        """
        Загружает тело SVG-схемы по переданному URL.
        """
        own_client = False
        if client is None:
            client = httpx.AsyncClient()
            own_client = True

        try:
            start_time = time.time()
            resp = await client.get(svg_url)
            elapsed = time.time() - start_time
            if resp.status_code == 200:
                logger.debug(f"[Fetcher] SVG fetched in {elapsed:.3f}s (size: {len(resp.text)} chars)")
                return resp.text
            return None
        except Exception as e:
            logger.warning(f"[Fetcher] Error fetching SVG from {svg_url}: {e}")
            return None
        finally:
            if own_client:
                await client.aclose()

    # Совместимость:
    async def start(
        self,
        client: httpx.AsyncClient | None = None,
    ) -> tuple[dict[str, str], dict[str, str], str] | None:
        """
        Инициализирует сессию: параллельно запрашивает страницу и схему.
        Возвращает (cookies, headers, svg_url).
        """
        own_client = False
        if client is None:
            client = httpx.AsyncClient(base_url=self.event_url)
            own_client = True

        try:
            page_res, svg_url = await asyncio.gather(
                self.fetch_page(event_id=self.event_id, client=client),
                self.fetch_scheme_url(event_id=self.event_id, client=client),
            )
            if not page_res or not svg_url:
                return None

            html_text, cookies, csrf_token = page_res
            headers = self.headers_template.copy()
            if csrf_token:
                headers["X-CSRF-Token"] = csrf_token

            return cookies, headers, svg_url
        finally:
            if own_client:
                await client.aclose()