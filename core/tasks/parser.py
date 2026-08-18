import re
import asyncio
import queue
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Iterable


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


class BaseParser(ABC):
    @abstractmethod
    def parse(
        self,
        svg_text: str,
        queue: queue.Queue | asyncio.Queue | None = None
    ) -> list[Ticket]:
        pass


class DefaultParser(BaseParser):
    def __init__(
        self,
        allowed_price_ids: Iterable[str] | None = None,
        allowed_sectors: Iterable[str] | None = None,
        filter_fn: Callable[[Ticket], bool] | None = None
    ) -> None:
        self.allowed_price_ids = set(allowed_price_ids) if allowed_price_ids is not None else None
        self.allowed_sectors = set(allowed_sectors) if allowed_sectors is not None else None
        self.filter_fn = filter_fn

        self.g_pattern = re.compile(
            r'<g\b(?P<attrs>[^>]*\bprice_id="(?P<p>\d+)"[^>]*)>(?P<content>.*?)</g>',
            re.DOTALL
        )
        self.circle_pattern = re.compile(
            r'<circle\s+id="(?P<t>\d+)"(?:\s+[^>]*?name="(?P<n>[^"]*)")?'
        )

    def parse(
        self,
        svg_text: str,
        queue: queue.Queue | asyncio.Queue | None = None
    ) -> list[Ticket]:
        results: list[Ticket] = []

        for g_match in self.g_pattern.finditer(svg_text):
            attrs = g_match.group("attrs")
            if 'fill="#999999"' in attrs or 'fill="#c0c0c0"' in attrs.lower():
                continue

            price_id = g_match.group("p")

            if self.allowed_price_ids is not None and price_id not in self.allowed_price_ids:
                continue

            content = g_match.group("content")

            for c_match in self.circle_pattern.finditer(content):
                ticket_id = c_match.group("t")
                name = c_match.group("n") or ""
                ticket = Ticket(ticket_id=ticket_id, price_id=price_id, name=name)

                if self.allowed_sectors is not None and ticket.sector not in self.allowed_sectors:
                    continue

                if self.filter_fn is not None and not self.filter_fn(ticket):
                    continue

                if queue is not None:
                    queue.put_nowait(ticket)
                    continue
                results.append(ticket)

        return results


Parser = DefaultParser