import asyncio
import json
import logging
import time
import uuid
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.tasks.payloads import BookedTicketPayload, BookingItem
from saas.service.allocation import ActiveUserTask
from saas.storage.models import Base, BookingModel, RoleModel, UserModel, UserTaskModel

logger = logging.getLogger("saas.storage.orchestrator")

DEFAULT_ROLES = [
    {"name": "GUEST", "max_active_tasks": 1, "priority_level": 0, "can_target_exclusive": False},
    {"name": "USER", "max_active_tasks": 3, "priority_level": 1, "can_target_exclusive": False},
    {"name": "VIP", "max_active_tasks": 10, "priority_level": 10, "can_target_exclusive": True},
    {"name": "ADMIN", "max_active_tasks": 50, "priority_level": 100, "can_target_exclusive": True},
]


class StorageOrchestrator:
    """
    Оркестратор персистентного хранилища (SQLAlchemy 2.0 Async).
    Поддерживает SQLite WAL (локально) и PostgreSQL (продакшн).
    Инкапсулирует CRUD, фоновую очистку TTL (30с) и Crash Recovery.
    """

    def __init__(self, db_url: str = "sqlite+aiosqlite:///sniper.db") -> None:
        self.db_url = db_url
        self.engine = create_async_engine(
            self.db_url,
            echo=False,
            future=True,
        )
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )
        self._cleanup_task: asyncio.Task[Any] | None = None
        self._is_running = True

    async def init_db(self) -> None:
        """Создает таблицы и сидирует дефолтные роли."""
        async with self.engine.begin() as conn:
            # Для SQLite включаем WAL-режим
            if "sqlite" in self.db_url:
                await conn.exec_driver_sql("PRAGMA journal_mode=WAL;")
                await conn.exec_driver_sql("PRAGMA synchronous=NORMAL;")
            await conn.run_sync(Base.metadata.create_all)

        # Сидирование ролей
        async with self.session_factory() as session:
            for r_data in DEFAULT_ROLES:
                existing = await session.get(RoleModel, r_data["name"])
                if not existing:
                    session.add(RoleModel(**r_data))
            await session.commit()
        logger.info(f"[StorageOrchestrator] Database initialized at {self.db_url}")

    async def get_or_create_user(
        self,
        user_id: str | None = None,
        role_name: str = "GUEST",
        external_id: str | None = None,
    ) -> UserModel:
        uid = user_id or str(uuid.uuid4())
        async with self.session_factory() as session:
            user = await session.get(UserModel, uid)
            if not user:
                if external_id:
                    # Поиск по external_id (например, telegram:123)
                    stmt = select(UserModel).where(UserModel.external_id == external_id)
                    res = await session.execute(stmt)
                    user = res.scalar_one_or_none()

                if not user:
                    user = UserModel(id=uid, role_name=role_name, external_id=external_id)
                    session.add(user)
                    await session.commit()
                    await session.refresh(user)
            return user

    async def create_task(
        self,
        user_id: str,
        event_id: str,
        target_tickets: int = 1,
        filter_boxes: list[int] | tuple[int, ...] | None = None,
    ) -> UserTaskModel:
        task_id = str(uuid.uuid4())
        boxes = list(filter_boxes or [])
        task = UserTaskModel(
            id=task_id,
            user_id=user_id,
            event_id=str(event_id),
            target_tickets=target_tickets,
            booked_count=0,
            filter_boxes_json=json.dumps(boxes),
            status="active",
        )
        async with self.session_factory() as session:
            session.add(task)
            await session.commit()
            await session.refresh(task)
            logger.info(f"[StorageOrchestrator] Created task {task.id} for user {user_id} on event {event_id}")
            return task

    async def get_active_tasks(self) -> list[UserTaskModel]:
        async with self.session_factory() as session:
            stmt = select(UserTaskModel).where(
                UserTaskModel.status == "active",
                UserTaskModel.booked_count < UserTaskModel.target_tickets,
            )
            res = await session.execute(stmt)
            return list(res.scalars().all())

    async def save_booking(
        self,
        task_id: str,
        user_id: str,
        payload: BookedTicketPayload,
    ) -> BookingModel:
        booking_id = str(uuid.uuid4())
        booking = BookingModel(
            id=booking_id,
            task_id=task_id,
            user_id=user_id,
            event_id=payload.event_id,
            ticket_id=payload.ticket_id,
            price_id=payload.price_id,
            seat_info=payload.seat_info,
            price_value=payload.price_value,
            session_cookies_json=json.dumps(payload.session_cookies),
            status="unclaimed",
            booked_at=payload.booked_at,
            expires_at=payload.expires_at,
        )
        async with self.session_factory() as session:
            session.add(booking)
            # Обновление счетчика задачи в БД
            task = await session.get(UserTaskModel, task_id)
            if task:
                task.booked_count += 1
                if task.booked_count >= task.target_tickets:
                    task.status = "completed"
            await session.commit()
            await session.refresh(booking)
            logger.info(f"[StorageOrchestrator] Persisted booking {booking.id} for task {task_id}")
            return booking

    async def claim_booking(self, booking_id: str, user_id: str) -> BookingItem | None:
        async with self.session_factory() as session:
            booking = await session.get(BookingModel, booking_id)
            if not booking or booking.user_id != user_id:
                return None

            now = time.time()
            if booking.expires_at < now:
                booking.status = "expired"
                await session.commit()
                return None

            booking.status = "claimed"
            booking.claimed_at = now
            await session.commit()

            time_left = max(0, int(booking.expires_at - now))
            return BookingItem(
                booking_id=booking.id,
                ticket_id=booking.ticket_id,
                seat_info=booking.seat_info,
                price=booking.price_value,
                expires_at=booking.expires_at,
                time_left_sec=time_left,
                cookies=booking.session_cookies,
            )

    async def get_user_bookings(self, user_id: str, active_only: bool = True) -> list[BookingItem]:
        now = time.time()
        async with self.session_factory() as session:
            stmt = select(BookingModel).where(BookingModel.user_id == user_id)
            if active_only:
                stmt = stmt.where(
                    BookingModel.status == "unclaimed",
                    BookingModel.expires_at > now,
                )
            res = await session.execute(stmt)
            bookings = res.scalars().all()

            return [
                BookingItem(
                    booking_id=b.id,
                    ticket_id=b.ticket_id,
                    seat_info=b.seat_info,
                    price=b.price_value,
                    expires_at=b.expires_at,
                    time_left_sec=max(0, int(b.expires_at - now)),
                    cookies=b.session_cookies,
                )
                for b in bookings
            ]

    async def expire_outdated_bookings(self, now: float | None = None) -> int:
        current_time = now or time.time()
        expired_count = 0
        async with self.session_factory() as session:
            # Находим все протухшие неклеймнутые брони
            stmt = select(BookingModel).where(
                BookingModel.status == "unclaimed",
                BookingModel.expires_at <= current_time,
            )
            res = await session.execute(stmt)
            outdated = res.scalars().all()

            for b in outdated:
                b.status = "expired"
                expired_count += 1
                # Коррекция счетчика задачи
                task = await session.get(UserTaskModel, b.task_id)
                if task and task.status == "completed":
                    task.status = "active"
                    task.booked_count = max(0, task.booked_count - 1)

            if expired_count > 0:
                await session.commit()
                logger.info(f"[StorageOrchestrator] Expired {expired_count} outdated bookings")

        return expired_count

    async def recover_on_startup(self) -> tuple[list[ActiveUserTask], list[str]]:
        """
        Восстановление состояния после сбоя/перезапуска сервера:
        1. Помечает истёкшие за время простоя брони как 'expired'.
        2. Извлекает все незавершенные активные задачи.
        3. Возвращает (active_tasks_for_ram, unique_event_ids_to_resume).
        """
        await self.expire_outdated_bookings()
        tasks = await self.get_active_tasks()

        ram_tasks: list[ActiveUserTask] = []
        unique_events: set[str] = set()

        for t in tasks:
            unique_events.add(t.event_id)
            ram_tasks.append(
                ActiveUserTask(
                    task_id=t.id,
                    user_id=t.user_id,
                    event_id=t.event_id,
                    target_tickets=t.target_tickets,
                    booked_count=t.booked_count,
                    filter_boxes=t.filter_boxes,
                    created_at=t.created_at,
                )
            )

        logger.info(
            f"[StorageOrchestrator] Crash Recovery complete: restored {len(ram_tasks)} active tasks "
            f"across {len(unique_events)} events"
        )
        return ram_tasks, list(unique_events)

    def start_cleanup_loop(self, interval_sec: float = 30.0) -> None:
        self._is_running = True
        self._cleanup_task = asyncio.create_task(self._periodic_cleanup(interval_sec))

    async def _periodic_cleanup(self, interval_sec: float) -> None:
        while self._is_running:
            try:
                await asyncio.sleep(interval_sec)
                await self.expire_outdated_bookings()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[StorageOrchestrator] Error in periodic cleanup: {e}")

    async def close(self) -> None:
        self._is_running = False
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except (asyncio.CancelledError, Exception):
                pass
        await self.engine.dispose()
