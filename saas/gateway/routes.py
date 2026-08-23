import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from saas.gateway.auth import AuthCredentials, CompositeTokenHandler
from saas.gateway.orchestrator import SaaSGatewayOrchestrator

router = APIRouter(prefix="/api/gateway", tags=["SaaS Gateway"])


# Pydantic схемы запросов
class CreateTaskRequest(BaseModel):
    event_id: str = Field(..., description="ID мероприятия на Ticketpro")
    target_tickets: int = Field(1, ge=1, le=10, description="Желаемое количество билетов")
    filter_boxes: list[int] = Field(default_factory=list, description="Список 64-битных SpatialBox фильтров")


class ClaimBookingRequest(BaseModel):
    booking_id: str = Field(..., description="ID брони для перевода в корзину")


def get_current_user(
    request: Request,
    response: Response,
) -> AuthCredentials:
    """
    Dependency: извлекает и верифицирует токен из Cookie или заголовка Authorization / X-Service-Token.
    Если токен отсутствует — автоматически генерирует анонимный GUEST токен.
    """
    orchestrator: SaaSGatewayOrchestrator = request.app.state.gateway_orchestrator
    token_handler = orchestrator.token_handler

    raw_token = (
        request.cookies.get("sniper_token")
        or request.headers.get("X-Service-Token")
        or (
            auth_header.split(" ", 1)[1]
            if (auth_header := request.headers.get("Authorization", "")).startswith("Bearer ")
            else None
        )
    )

    creds = token_handler.verify_token(raw_token) if raw_token else None
    if not creds:
        # Автоматическая генерация гостевой сессии
        guest_id = f"guest_{uuid.uuid4().hex[:12]}"
        new_token = token_handler.create_token(user_id=guest_id, role_name="GUEST")
        response.set_cookie(
            key="sniper_token",
            value=new_token,
            httponly=True,
            samesite="lax",
            max_age=86400 * 30,  # 30 дней
        )
        creds = AuthCredentials(user_id=guest_id, role_name="GUEST", token_type="hmac")

    return creds


@router.post("/auth/guest")
async def auth_guest(
    request: Request,
    response: Response,
) -> dict[str, Any]:
    """Создает гостевой токен и устанавливает куку."""
    orchestrator: SaaSGatewayOrchestrator = request.app.state.gateway_orchestrator
    guest_id = f"guest_{uuid.uuid4().hex[:12]}"
    token = orchestrator.token_handler.create_token(user_id=guest_id, role_name="GUEST")

    response.set_cookie(
        key="sniper_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=86400 * 30,
    )
    return {
        "user_id": guest_id,
        "role": "GUEST",
        "token": token,
    }


@router.post("/tasks")
async def create_task(
    payload: CreateTaskRequest,
    request: Request,
    user: AuthCredentials = Depends(get_current_user),
) -> dict[str, Any]:
    """Создает задачу на снайпинг мест с заданными пространственными фильтрами."""
    orchestrator: SaaSGatewayOrchestrator = request.app.state.gateway_orchestrator
    try:
        task = await orchestrator.create_user_task(
            user_id=user.user_id,
            event_id=payload.event_id,
            target_tickets=payload.target_tickets,
            filter_boxes=payload.filter_boxes,
        )
        return {
            "status": "success",
            "task_id": task.id,
            "event_id": task.event_id,
            "target_tickets": task.target_tickets,
            "booked_count": task.booked_count,
            "created_at": task.created_at,
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/tasks")
async def get_tasks(
    request: Request,
    user: AuthCredentials = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Возвращает все задачи текущего пользователя."""
    orchestrator: SaaSGatewayOrchestrator = request.app.state.gateway_orchestrator
    tasks = await orchestrator.get_user_tasks(user_id=user.user_id)
    return [
        {
            "task_id": t.id,
            "event_id": t.event_id,
            "target_tickets": t.target_tickets,
            "booked_count": t.booked_count,
            "status": t.status,
            "filter_boxes": t.filter_boxes,
            "created_at": t.created_at,
        }
        for t in tasks
    ]


@router.delete("/tasks/{task_id}")
async def cancel_task(
    task_id: str,
    request: Request,
    user: AuthCredentials = Depends(get_current_user),
) -> dict[str, Any]:
    """Отменяет задачу пользователя."""
    orchestrator: SaaSGatewayOrchestrator = request.app.state.gateway_orchestrator
    success = await orchestrator.cancel_user_task(user_id=user.user_id, task_id=task_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return {"status": "cancelled", "task_id": task_id}


@router.post("/bookings/claim")
async def claim_booking(
    payload: ClaimBookingRequest,
    request: Request,
    user: AuthCredentials = Depends(get_current_user),
) -> dict[str, Any]:
    """Забирает пойманную бронь (возвращает сессионные куки и метаданные)."""
    orchestrator: SaaSGatewayOrchestrator = request.app.state.gateway_orchestrator
    item = await orchestrator.claim_booking(user_id=user.user_id, booking_id=payload.booking_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found or expired")

    return {
        "booking_id": item.booking_id,
        "ticket_id": item.ticket_id,
        "seat_info": item.seat_info,
        "price": item.price,
        "expires_at": item.expires_at,
        "time_left_sec": item.time_left_sec,
        "cookies": item.cookies,
    }


@router.get("/bookings")
async def get_bookings(
    request: Request,
    active_only: bool = True,
    user: AuthCredentials = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Возвращает список активных неистекших броней пользователя."""
    orchestrator: SaaSGatewayOrchestrator = request.app.state.gateway_orchestrator
    items = await orchestrator.get_user_bookings(user_id=user.user_id, active_only=active_only)
    return [
        {
            "booking_id": b.booking_id,
            "ticket_id": b.ticket_id,
            "seat_info": b.seat_info,
            "price": b.price,
            "expires_at": b.expires_at,
            "time_left_sec": b.time_left_sec,
            "cookies": b.cookies,
        }
        for b in items
    ]


@router.get("/stream")
async def sse_event_stream(
    request: Request,
    user: AuthCredentials = Depends(get_current_user),
) -> StreamingResponse:
    """SSE live-стрим событий пользователя."""
    orchestrator: SaaSGatewayOrchestrator = request.app.state.gateway_orchestrator
    return StreamingResponse(
        orchestrator.stream_user_events(user_id=user.user_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
