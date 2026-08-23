import asyncio
import html
import json
import logging
import queue
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Iterable

logger = logging.getLogger("core.parser")


@dataclass(slots=True)
class Ticket:
    ticket_id: str
    price_id: str
    name: str = ""

    @property
    def sector(self) -> str:
        match = re.search(r"2:([^/]+)", self.name)
        return match.group(1) if match else ""

    @property
    def row(self) -> str:
        match = re.search(r"3:([^/]+)", self.name)
        return match.group(1) if match else ""

    @property
    def seat(self) -> str:
        match = re.search(r"4:([^/]+)", self.name)
        return match.group(1) if match else ""

    def __iter__(self):
        yield self.ticket_id
        yield self.price_id


from core.spatial import spatial_encoder
from core.tasks.payloads import FilterSnapshot


class BaseParser(ABC):
    @abstractmethod
    def parse(
        self,
        svg_text: str,
        queue: queue.Queue | asyncio.Queue | None = None
    ) -> list[Ticket]:
        pass

    @staticmethod
    def extract_event_prices(html_text: str) -> dict[str, dict[str, Any]]:
        """
        Извлекает блок цен и категорий события из HTML-текста страницы.
        Возвращает словарь вида: {price_id: {"id": str, "price": float, "color": str, ...}}
        """
        return DefaultParser.extract_event_prices(html_text)


class DefaultParser(BaseParser):
    def __init__(
        self,
        allowed_price_ids: Iterable[str] | None = None,
        allowed_sectors: Iterable[str] | None = None,
        filter_fn: Callable[[Ticket], bool] | None = None,
        valid_price_ids: set[str] | None = None,
        snapshot: FilterSnapshot | None = None,
    ) -> None:
        self.allowed_price_ids = set(allowed_price_ids) if allowed_price_ids is not None else None
        self._valid_price_ids = valid_price_ids if valid_price_ids is not None else self.allowed_price_ids
        self._allowed_sectors = set(allowed_sectors) if allowed_sectors is not None else None
        self.filter_fn = filter_fn
        self.snapshot = snapshot

        self.g_pattern = re.compile(
            r'<g\b(?P<attrs>[^>]*)>(?P<content>.*?)</g>',
            re.DOTALL
        )
        self.price_id_pattern = re.compile(
            r'\b(?:price_id|priceId)="(?P<p>\d+)"'
        )
        self.circle_pattern = re.compile(
            r'<circle\s+id="(?P<t>\d+)"(?:\s+[^>]*?name="(?P<n>[^"]*)")?'
        )

    @property
    def valid_price_ids(self) -> set[str] | frozenset[str] | None:
        if self.snapshot and self.snapshot.valid_price_ids:
            return self.snapshot.valid_price_ids
        return self._valid_price_ids

    @valid_price_ids.setter
    def valid_price_ids(self, val: set[str] | frozenset[str] | None) -> None:
        self._valid_price_ids = val

    @property
    def allowed_sectors(self) -> set[str] | frozenset[str] | None:
        if self.snapshot and self.snapshot.allowed_sectors:
            return self.snapshot.allowed_sectors
        return self._allowed_sectors

    @allowed_sectors.setter
    def allowed_sectors(self, val: set[str] | frozenset[str] | None) -> None:
        self._allowed_sectors = val

    def set_snapshot(self, snapshot: FilterSnapshot) -> None:
        """Атомарная подмена ссылки на иммутабельный снимок (Zero-Lock Atomic Swap)."""
        self.snapshot = snapshot

    @staticmethod
    def extract_event_prices(html_text: str) -> dict[str, dict[str, Any]]:
        """
        Парсит блок с price_id и ценами из HTML страницы события (req.text).
        Ищет переменную JS prices_of_event или input/data теги с JSON-структурой.
        """
        prices: dict[str, dict[str, Any]] = {}
        if not html_text:
            return prices

        raw_json_str = None

        # Шаблон 1: JS переменная var/let/const prices_of_event = '...'; или prices_of_event = "...";
        m_var = re.search(r'prices_of_event\s*=\s*[\'"](.*?)[\'"]\s*;', html_text, re.DOTALL)
        if m_var:
            raw_json_str = m_var.group(1)
        else:
            # Шаблон 2: JS объект без кавычек prices_of_event = { ... };
            m_obj = re.search(r'prices_of_event\s*=\s*(\{.*?\})\s*;', html_text, re.DOTALL)
            if m_obj:
                raw_json_str = m_obj.group(1)
            else:
                # Шаблон 3: HTML тег <input ... name="prices_of_event" ... value="...">
                m_tag = re.search(
                    r'<(?:input|div)[^>]*?(?:name|id|data-name)=[\'"]prices_of_event[\'"][^>]*?value=[\'"](.*?)[\'"]',
                    html_text,
                    re.IGNORECASE | re.DOTALL
                )
                if m_tag:
                    raw_json_str = m_tag.group(1)

        if raw_json_str:
            try:
                unescaped = html.unescape(raw_json_str).strip()
                data = json.loads(unescaped)
                if isinstance(data, dict):
                    for pid, pdata in data.items():
                        pid_str = str(pid)
                        if isinstance(pdata, dict):
                            raw_p = str(pdata.get("price", "0")).replace(",", ".")
                            try:
                                num_price = float(raw_p)
                            except ValueError:
                                num_price = 0.0
                            prices[pid_str] = {
                                "id": pid_str,
                                "price": num_price,
                                "color": str(pdata.get("color", "#3b82f6")),
                                "raw_price": str(pdata.get("price", "0")),
                                "obstructed_view": str(pdata.get("obstructed_view", "0")),
                                "widget_code": pdata.get("widget_code"),
                            }
                        else:
                            try:
                                num_price = float(str(pdata).replace(",", "."))
                            except ValueError:
                                num_price = 0.0
                            prices[pid_str] = {
                                "id": pid_str,
                                "price": num_price,
                                "color": "#3b82f6",
                                "raw_price": str(pdata),
                            }
            except Exception as e:
                logger.warning(f"Failed to parse prices_of_event JSON: {e}")

        return prices

    def parse(
        self,
        svg_text: str,
        queue: queue.Queue | asyncio.Queue | None = None
    ) -> list[Ticket]:
        results: list[Ticket] = []
        total_g_found = 0
        total_circles_found = 0
        skipped_gray = 0
        skipped_price = 0
        skipped_sector = 0

        for g_match in self.g_pattern.finditer(svg_text):
            total_g_found += 1
            attrs = g_match.group("attrs")
            if 'fill="#999999"' in attrs or 'fill="#c0c0c0"' in attrs.lower() or 'fill="#939393"' in attrs.lower():
                skipped_gray += 1
                continue

            p_match = self.price_id_pattern.search(attrs)
            group_price_id = p_match.group("p") if p_match else ""

            # Проверка по snapshot или по старым полям (fallback)
            current_snapshot = self.snapshot
            valid_prices = current_snapshot.valid_price_ids if current_snapshot and current_snapshot.valid_price_ids else (self.valid_price_ids or self.allowed_price_ids)
            allowed_sectors = current_snapshot.allowed_sectors if current_snapshot and current_snapshot.allowed_sectors else self.allowed_sectors

            if valid_prices and group_price_id and group_price_id not in valid_prices:
                skipped_price += 1
                continue

            content = g_match.group("content")

            for c_match in self.circle_pattern.finditer(content):
                total_circles_found += 1
                ticket_id = c_match.group("t")
                name = c_match.group("n") or ""
                ticket = Ticket(ticket_id=ticket_id, price_id=group_price_id, name=name)

                if allowed_sectors and ticket.sector not in allowed_sectors:
                    skipped_sector += 1
                    continue

                # Если в snapshot есть spatial filter_boxes — проверяем соответствие
                if current_snapshot and current_snapshot.filter_boxes:
                    matched_box = False
                    row_int = int(ticket.row) if ticket.row.isdigit() else 0
                    seat_int = int(ticket.seat) if ticket.seat.isdigit() else 0
                    loc_id = int(ticket.sector) if ticket.sector.isdigit() else 0

                    for box in current_snapshot.filter_boxes:
                        if spatial_encoder.is_match(
                            packed_box=box,
                            location_id=loc_id,
                            row=row_int,
                            seat=seat_int,
                        ):
                            matched_box = True
                            break
                    if not matched_box:
                        continue

                if self.filter_fn is not None and not self.filter_fn(ticket):
                    continue

                if queue is not None:
                    queue.put_nowait(ticket)
                results.append(ticket)

        logger.info(
            f"[Parser] SVG parsed: groups={total_g_found} (gray={skipped_gray}, skipped_price={skipped_price}), "
            f"seats_found={total_circles_found} (skipped_sector={skipped_sector}), "
            f"valid_tickets_matched={len(results)}"
        )
        return results



Parser = DefaultParser