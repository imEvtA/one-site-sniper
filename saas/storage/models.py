import json
import time
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class RoleModel(Base):
    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    max_active_tasks: Mapped[int] = mapped_column(Integer, default=1)
    priority_level: Mapped[int] = mapped_column(Integer, default=0)
    can_target_exclusive: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)

    users: Mapped[list["UserModel"]] = relationship("UserModel", back_populates="role")


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    role_name: Mapped[str] = mapped_column(String(50), ForeignKey("roles.name"), default="GUEST")
    external_id: Mapped[str | None] = mapped_column(String(100), unique=True, index=True, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)

    role: Mapped["RoleModel"] = relationship("RoleModel", back_populates="users")
    tasks: Mapped[list["UserTaskModel"]] = relationship("UserTaskModel", back_populates="user", cascade="all, delete-orphan")
    bookings: Mapped[list["BookingModel"]] = relationship("BookingModel", back_populates="user", cascade="all, delete-orphan")


class UserTaskModel(Base):
    __tablename__ = "user_tasks"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(100), ForeignKey("users.id"), index=True)
    event_id: Mapped[str] = mapped_column(String(100), index=True)
    target_tickets: Mapped[int] = mapped_column(Integer, default=1)
    booked_count: Mapped[int] = mapped_column(Integer, default=0)
    filter_boxes_json: Mapped[str] = mapped_column(Text, default="[]")  # JSON-массив uint64
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)  # active, completed, paused, cancelled
    created_at: Mapped[float] = mapped_column(Float, default=time.time)

    user: Mapped["UserModel"] = relationship("UserModel", back_populates="tasks")
    bookings: Mapped[list["BookingModel"]] = relationship("BookingModel", back_populates="task", cascade="all, delete-orphan")

    @property
    def filter_boxes(self) -> tuple[int, ...]:
        try:
            return tuple(json.loads(self.filter_boxes_json))
        except Exception:
            return ()


class BookingModel(Base):
    __tablename__ = "bookings"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(100), ForeignKey("user_tasks.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(100), ForeignKey("users.id"), index=True)
    event_id: Mapped[str] = mapped_column(String(100), index=True)
    ticket_id: Mapped[str] = mapped_column(String(100))
    price_id: Mapped[str] = mapped_column(String(100))
    seat_info: Mapped[str] = mapped_column(Text, default="")
    price_value: Mapped[float] = mapped_column(Float, default=0.0)
    session_cookies_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(30), default="unclaimed", index=True)  # unclaimed, claimed, expired
    booked_at: Mapped[float] = mapped_column(Float, default=time.time)
    expires_at: Mapped[float] = mapped_column(Float, index=True)  # booked_at + 600
    claimed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)

    user: Mapped["UserModel"] = relationship("UserModel", back_populates="bookings")
    task: Mapped["UserTaskModel"] = relationship("UserTaskModel", back_populates="bookings")

    @property
    def session_cookies(self) -> dict[str, str]:
        try:
            return json.loads(self.session_cookies_json)
        except Exception:
            return {}
