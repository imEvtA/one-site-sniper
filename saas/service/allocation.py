import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

from core.spatial import spatial_encoder
from core.tasks.payloads import BookedTicketPayload, FilterSnapshot
from saas.service.bus import BaseEventBus

logger = logging.getLogger("saas.service.allocation")


@dataclass
class ActiveUserTask:
    """
    In-memory представление активной пользовательской задачи на снайпинг.
    """
    task_id: str
    user_id: str
    event_id: str
    target_tickets: int = 1
    booked_count: int = 0
    filter_boxes: tuple[int, ...] = field(default_factory=tuple)  # Кортеж 64-битных упакованных фильтров
    priority_level: int = 0
    created_at: float = field(default_factory=time.time)

    @property
    def is_completed(self) -> bool:
        return self.booked_count >= self.target_tickets


class BaseAllocationStrategy(ABC):
    """
    Абстрактный контракт выбора побеждающей задачи среди подходящих кандидатов.
    """
    @abstractmethod
    def select_recipient(
        self,
        candidate_tasks: list[ActiveUserTask],
        payload: BookedTicketPayload,
    ) -> ActiveUserTask | None:
        pass


class FairShareAllocationStrategy(BaseAllocationStrategy):
    """
    Честная стратегия распределения (Fair Share / Round-Robin):
    1. Наивысший приоритет роли (priority_level DESC).
    2. Наименьшее число уже пойманных билетов (booked_count ASC).
    3. Самая старая задача в очереди (created_at ASC).
    """
    def select_recipient(
        self,
        candidate_tasks: list[ActiveUserTask],
        payload: BookedTicketPayload,
    ) -> ActiveUserTask | None:
        if not candidate_tasks:
            return None

        return min(
            candidate_tasks,
            key=lambda t: (-t.priority_level, t.booked_count, t.created_at),
        )


class AllocationManager:
    """
    Диспетчер сопоставления и распределения билетов.
    Мгновенно матчит пойманные консьюмерами билеты с активными задачами пользователей в RAM.
    """

    def __init__(
        self,
        event_bus: BaseEventBus,
        strategy: BaseAllocationStrategy | None = None,
        on_booking_persisted: Callable[[ActiveUserTask, BookedTicketPayload], Any] | None = None,
        on_snapshot_changed: Callable[[str, FilterSnapshot], Any] | None = None,
    ) -> None:
        self.event_bus = event_bus
        self.strategy = strategy or FairShareAllocationStrategy()
        self.on_booking_persisted = on_booking_persisted
        self.on_snapshot_changed = on_snapshot_changed
        self._tasks_by_event: dict[str, dict[str, ActiveUserTask]] = {}
        self._lock = asyncio.Lock()

    async def register_task(self, task: ActiveUserTask) -> None:
        async with self._lock:
            if task.event_id not in self._tasks_by_event:
                self._tasks_by_event[task.event_id] = {}
            self._tasks_by_event[task.event_id][task.task_id] = task
            logger.info(f"[AllocationManager] Registered task {task.task_id} for user {task.user_id} on event {task.event_id}")

        self._notify_snapshot_changed(task.event_id)

    async def unregister_task(self, event_id: str, task_id: str) -> None:
        async with self._lock:
            if event_id in self._tasks_by_event:
                self._tasks_by_event[event_id].pop(task_id, None)
                if not self._tasks_by_event[event_id]:
                    del self._tasks_by_event[event_id]
                logger.info(f"[AllocationManager] Unregistered task {task_id}")

        self._notify_snapshot_changed(event_id)

    def _notify_snapshot_changed(self, event_id: str) -> None:
        if self.on_snapshot_changed:
            snap = self.get_filter_snapshot(event_id)
            res = self.on_snapshot_changed(event_id, snap)
            if asyncio.iscoroutine(res):
                asyncio.create_task(res)

    def get_event_tasks(self, event_id: str) -> list[ActiveUserTask]:
        return list(self._tasks_by_event.get(event_id, {}).values())

    def get_filter_snapshot(self, event_id: str) -> FilterSnapshot:
        """
        Формирует объединенный снимок фильтров активного спроса для BotSession.
        """
        tasks = self.get_event_tasks(event_id)
        active_boxes: list[int] = []
        for t in tasks:
            if not t.is_completed:
                active_boxes.extend(t.filter_boxes)

        return FilterSnapshot(filter_boxes=tuple(active_boxes))

    async def allocate_ticket(
        self,
        payload: BookedTicketPayload,
        location_id: int = 0,
        row: int = 0,
        seat: int = 0,
        price_index: int = 0,
    ) -> ActiveUserTask | None:
        """
        Сопоставляет пойманный билет с активными задачами, выбирает получателя,
        атомарно обновляет счетчик и публикует событие аллокации.
        """
        loc = payload.location_id or location_id
        r = payload.row or row
        s = payload.seat or seat
        p_idx = payload.price_index or price_index

        winner: ActiveUserTask | None = None
        is_now_completed = False

        async with self._lock:
            event_tasks = list(self._tasks_by_event.get(payload.event_id, {}).values())
            if not event_tasks:
                logger.warning(f"[AllocationManager] No active tasks for event {payload.event_id} to receive ticket {payload.ticket_id}")
                return None

            # 1. Поиск задач-кандидатов, чьи пространственные фильтры соответствуют билету
            candidates: list[ActiveUserTask] = []
            for task in event_tasks:
                if task.is_completed:
                    continue

                if not task.filter_boxes:
                    candidates.append(task)
                    continue

                for box in task.filter_boxes:
                    if spatial_encoder.is_match(
                        packed_box=box,
                        location_id=loc,
                        row=r,
                        seat=s,
                        price_index=p_idx,
                    ):
                        candidates.append(task)
                        break

            if not candidates:
                logger.info(f"[AllocationManager] Ticket {payload.ticket_id} did not match any active candidate filters")
                return None

            # 2. Выбор побеждающей задачи через стратегию
            winner = self.strategy.select_recipient(candidates, payload)
            if not winner:
                return None

            winner.booked_count += 1
            is_now_completed = winner.is_completed

            # Если задача завершена — удаляем из очереди активных
            if is_now_completed and payload.event_id in self._tasks_by_event:
                self._tasks_by_event[payload.event_id].pop(winner.task_id, None)
                if not self._tasks_by_event[payload.event_id]:
                    del self._tasks_by_event[payload.event_id]

        if is_now_completed:
            self._notify_snapshot_changed(payload.event_id)

        logger.info(
            f"[AllocationManager] 🎟️ Ticket {payload.ticket_id} allocated to user {winner.user_id} "
            f"(Task: {winner.task_id}, Progress: {winner.booked_count}/{winner.target_tickets})"
        )

        # 3. Уведомление персистентного слоя (сохранение в БД)
        if self.on_booking_persisted:
            res = self.on_booking_persisted(winner, payload)
            if asyncio.iscoroutine(res):
                await res

        # 4. Публикация события в шину (канал пользователя)
        user_channel = f"user.{winner.user_id}"
        await self.event_bus.publish(
            channel=user_channel,
            event_data={
                "type": "booking_allocated",
                "task_id": winner.task_id,
                "event_id": payload.event_id,
                "ticket_id": payload.ticket_id,
                "price_id": payload.price_id,
                "price_value": payload.price_value,
                "seat_info": payload.seat_info,
                "booked_at": payload.booked_at,
                "expires_at": payload.expires_at,
                "task_completed": is_now_completed,
            },
        )

        return winner
