import asyncio
import logging
import uuid
from typing import Any, AsyncGenerator, Callable

from core.bot import BotSession, PresessionData
from core.pipeline.context import PipelineContext
from core.tasks.payloads import BookedTicketPayload, BookingItem, FilterSnapshot
from saas.gateway.auth import BaseTokenHandler, CompositeTokenHandler
from saas.service.allocation import ActiveUserTask, AllocationManager, BaseAllocationStrategy, FairShareAllocationStrategy
from saas.service.bus import BaseEventBus, InMemoryEventBus
from saas.service.notifications import NotificationManager
from saas.storage.models import RoleModel, UserModel, UserTaskModel
from saas.storage.orchestrator import StorageOrchestrator

logger = logging.getLogger("saas.gateway.orchestrator")


class SaaSGatewayOrchestrator:
    """
    Главный оркестратор шлюза SaaS:
    Связывает хранилище (Storage Layer), диспетчеризацию (Allocation Layer),
    события (Event Bus / Notifications) и фоновые снайперы (Bot Engine).
    """

    def __init__(
        self,
        db_url: str = "sqlite+aiosqlite:///sniper.db",
        event_bus: BaseEventBus | None = None,
        token_handler: BaseTokenHandler | None = None,
        allocation_strategy: BaseAllocationStrategy | None = None,
        session_factory: Callable[[str, Callable[[BookedTicketPayload], Any]], Any] | None = None,
    ) -> None:
        self.db_url = db_url
        self.event_bus = event_bus or InMemoryEventBus()
        self.token_handler = token_handler or CompositeTokenHandler()
        self.storage = StorageOrchestrator(db_url=self.db_url)
        self.notification_mgr = NotificationManager(event_bus=self.event_bus)

        self.allocation_mgr = AllocationManager(
            event_bus=self.event_bus,
            strategy=allocation_strategy or FairShareAllocationStrategy(),
            on_booking_persisted=self._on_booking_persisted,
            on_snapshot_changed=self._on_snapshot_changed,
        )

        # Фабрика сессий (позволяет подменять на Mock в тестах)
        self.session_factory = session_factory or self._default_session_factory
        self._active_sessions: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._is_running = True

    def _default_session_factory(
        self,
        event_id: str,
        on_payload: Callable[[BookedTicketPayload], Any],
    ) -> BotSession:
        remaining_tasks = self.allocation_mgr.get_event_tasks(event_id)
        target = sum(max(0, t.target_tickets - t.booked_count) for t in remaining_tasks) or 1
        ctx = PipelineContext(
            event_id=event_id,
            event_name=f"Event #{event_id}",
            target_tickets=target,
            num_consumers=3,
        )
        return BotSession(
            ctx=ctx,
            on_payload_callback=on_payload,
        )

    async def start(self) -> None:
        """Инициализация БД, запуск TTL-клинера и Crash Recovery."""
        await self.storage.init_db()
        self.storage.start_cleanup_loop(interval_sec=30.0)

        # Восстановление активных задач из БД
        ram_tasks, unique_events = await self.storage.recover_on_startup()
        for task in ram_tasks:
            await self.allocation_mgr.register_task(task)

        for event_id in unique_events:
            await self._ensure_bot_session_running(event_id)

        logger.info("[SaaSGatewayOrchestrator] SaaS Gateway successfully started and recovered")

    async def shutdown(self) -> None:
        """Штатная остановка всех компонентов."""
        self._is_running = False
        async with self._lock:
            for event_id, session in list(self._active_sessions.items()):
                if hasattr(session, "stop"):
                    res = session.stop()
                    if asyncio.iscoroutine(res):
                        await res
            self._active_sessions.clear()

        await self.storage.close()
        logger.info("[SaaSGatewayOrchestrator] SaaS Gateway cleanly shutdown")

    async def _on_booking_persisted(self, winner: ActiveUserTask, payload: BookedTicketPayload) -> None:
        """Сохранение брони в персистентное хранилище."""
        await self.storage.save_booking(
            task_id=winner.task_id,
            user_id=winner.user_id,
            payload=payload,
        )

    def _on_snapshot_changed(self, event_id: str, snapshot: FilterSnapshot) -> None:
        """Реактивное обновление фильтра спроса и целевого количества в BotSession."""
        session = self._active_sessions.get(event_id)
        if not session:
            return

        remaining_tasks = [t for t in self.allocation_mgr.get_event_tasks(event_id) if not t.is_completed]
        if not remaining_tasks:
            logger.info(f"[SaaSGatewayOrchestrator] All tasks for event {event_id} fulfilled or cancelled. Stopping BotSession.")
            if hasattr(session, "stop"):
                session.stop()
            self._active_sessions.pop(event_id, None)
            return

        remaining_target = sum(max(0, t.target_tickets - t.booked_count) for t in remaining_tasks)
        if hasattr(session, "ctx"):
            session.ctx.target_tickets = remaining_target

        if hasattr(session, "update_filter_snapshot"):
            session.update_filter_snapshot(snapshot)

    async def _ensure_bot_session_running(self, event_id: str) -> None:
        async with self._lock:
            remaining_tasks = [t for t in self.allocation_mgr.get_event_tasks(event_id) if not t.is_completed]
            if not remaining_tasks:
                return

            remaining_target = sum(max(0, t.target_tickets - t.booked_count) for t in remaining_tasks)

            session = self._active_sessions.get(event_id)
            if session and getattr(session, "is_running", lambda: True)():
                if hasattr(session, "ctx"):
                    session.ctx.target_tickets = remaining_target
                snapshot = self.allocation_mgr.get_filter_snapshot(event_id)
                if hasattr(session, "update_filter_snapshot"):
                    session.update_filter_snapshot(snapshot)
                return

            snapshot = self.allocation_mgr.get_filter_snapshot(event_id)
            session = self.session_factory(event_id, self.allocation_mgr.allocate_ticket)

            if hasattr(session, "ctx"):
                session.ctx.target_tickets = remaining_target

            if hasattr(session, "update_filter_snapshot"):
                session.update_filter_snapshot(snapshot)

            if hasattr(session, "start"):
                res = session.start()
                if asyncio.iscoroutine(res):
                    await res

            self._active_sessions[event_id] = session
            logger.info(f"[SaaSGatewayOrchestrator] Started BotSession for event {event_id} (Target: {remaining_target})")

    # -------------------------------------------------------------------------
    # Публичные методы API
    # -------------------------------------------------------------------------

    async def create_user_task(
        self,
        user_id: str,
        event_id: str,
        target_tickets: int = 1,
        filter_boxes: list[int] | None = None,
    ) -> UserTaskModel:
        # 1. Проверка лимитов роли
        user = await self.storage.get_or_create_user(user_id=user_id)
        active_tasks = [t for t in await self.storage.get_active_tasks() if t.user_id == user_id]

        async with self.storage.session_factory() as session:
            role = await session.get(RoleModel, user.role_name)
            max_tasks = role.max_active_tasks if role else 1

        if len(active_tasks) >= max_tasks:
            raise ValueError(f"Task limit exceeded for role {user.role_name} (Max: {max_tasks})")

        # 2. Создание в БД
        boxes = filter_boxes or []
        db_task = await self.storage.create_task(
            user_id=user_id,
            event_id=event_id,
            target_tickets=target_tickets,
            filter_boxes=boxes,
        )

        # 3. Регистрация в диспетчере
        ram_task = ActiveUserTask(
            task_id=db_task.id,
            user_id=user_id,
            event_id=event_id,
            target_tickets=target_tickets,
            booked_count=0,
            filter_boxes=tuple(boxes),
            created_at=db_task.created_at,
        )
        await self.allocation_mgr.register_task(ram_task)

        # 4. Обеспечение работы снайпера
        await self._ensure_bot_session_running(event_id)

        return db_task

    async def cancel_user_task(self, user_id: str, task_id: str) -> bool:
        async with self.storage.session_factory() as session:
            task = await session.get(UserTaskModel, task_id)
            if not task or task.user_id != user_id:
                return False
            task.status = "cancelled"
            event_id = task.event_id
            await session.commit()

        await self.allocation_mgr.unregister_task(event_id=event_id, task_id=task_id)
        return True

    async def get_user_tasks(self, user_id: str) -> list[UserTaskModel]:
        async with self.storage.session_factory() as session:
            from sqlalchemy import select
            stmt = select(UserTaskModel).where(UserTaskModel.user_id == user_id)
            res = await session.execute(stmt)
            return list(res.scalars().all())

    async def claim_booking(self, user_id: str, booking_id: str) -> BookingItem | None:
        return await self.storage.claim_booking(booking_id=booking_id, user_id=user_id)

    async def get_user_bookings(self, user_id: str, active_only: bool = True) -> list[BookingItem]:
        return await self.storage.get_user_bookings(user_id=user_id, active_only=active_only)

    async def stream_user_events(self, user_id: str) -> AsyncGenerator[str, None]:
        async for sse_event in self.notification_mgr.stream_user_events(user_id=user_id):
            yield sse_event
