import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
import httpx

from core.bot import BotSession, BotStatus, PresessionData, bot_manager
from core.pipeline.exceptions import PreflightError
from core.schemas import StartBotRequest
from core.tasks.fetcher import Fetcher
from core.tasks.parser import CurrentTicketproParser

# ==========================================
# 0. Конфигурация Telegram-бота (Токен)
# ==========================================

BOT_TOKEN = "8133223714:AAEcUkyyd9vaEQs015tUiBC0MCUf4GeT7DU"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("tg_bot")


# ==========================================
# 1. Хелпер curl-запросов с авто-повторами
# ==========================================

async def curl_request_json(
    url: str,
    headers: dict[str, str] | None = None,
    max_retries: int = 10,
    delay: float = 0.5,
) -> Any:
    """
    Выполняет HTTP-запрос через curl с поддержкой повторов при 5xx и сбросах соединений.
    """
    def _exec():
        for attempt in range(1, max_retries + 1):
            cmd = ["curl", "-s", url]
            if headers:
                for k, v in headers.items():
                    cmd.extend(["-H", f"{k}: {v}"])
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip():
                try:
                    return json.loads(res.stdout)
                except Exception:
                    pass
            time.sleep(delay)
        return None

    return await asyncio.to_thread(_exec)


# ==========================================
# 2. Клиент API Виджета (widget.ticketpro.by)
# ==========================================

class WidgetClient:
    """
    Клиент для взаимодействия с новым API виджета (widget.ticketpro.by).
    """
    def __init__(self, event_id: str, widget_id: str = "9"):
        self.event_id = str(event_id)
        self.widget_id = str(widget_id)
        self.session_id: str | None = None
        self.event_name: str = f"Событие #{event_id}"
        self.prices: dict[str, dict[str, Any]] = {}
        self.is_hunting = False

    def get_headers(self) -> dict[str, str]:
        h = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": f"https://widget.ticketpro.by/event/{self.event_id}?widget_id={self.widget_id}",
            "Origin": "https://widget.ticketpro.by",
        }
        if self.session_id:
            h["X-Session-Id"] = self.session_id
        return h

    async def init_session(self) -> str | None:
        url = f"https://widget.ticketpro.by/ticketpro-api/site/v2/get-basket?widget_id={self.widget_id}"
        data = await curl_request_json(url, headers=self.get_headers(), max_retries=10)
        if data and isinstance(data, dict):
            self.session_id = data.get("session_id")
            logger.info(f"[WidgetClient] Получен session_id: {self.session_id}")
            return self.session_id
        return None

    async def load_event_and_prices(self) -> tuple[str, dict[str, dict[str, Any]]]:
        if not self.session_id:
            await self.init_session()

        headers = self.get_headers()

        # 1. Информация о событии
        ev_url = f"https://widget.ticketpro.by/api/v1/booking-service/events/{self.event_id}"
        ev_data = await curl_request_json(ev_url, headers=headers, max_retries=10)
        if ev_data and isinstance(ev_data, dict):
            self.event_name = ev_data.get("name") or self.event_name

        # 2. Список доступных ценовых категорий
        pr_url = f"https://widget.ticketpro.by/ticketpro-api/site/v2/get-event-prices-list/{self.event_id}?widget_id={self.widget_id}&session_id={self.session_id or ''}"
        pr_data = await curl_request_json(pr_url, headers=headers, max_retries=10)
        self.prices = {}
        if pr_data and isinstance(pr_data, list):
            for item in pr_data:
                pid = str(item.get("id"))
                self.prices[pid] = {
                    "price": float(item.get("price", 0)),
                    "color": item.get("color", ""),
                    "id": pid,
                }
        return self.event_name, self.prices

    async def fetch_available_places(self, allowed_price_ids: set[str] | None = None) -> list[dict[str, Any]]:
        headers = self.get_headers()
        url = f"https://widget.ticketpro.by/api/v1/booking-service/events/{self.event_id}/places?widgetId={self.widget_id}"
        data = await curl_request_json(url, headers=headers, max_retries=3, delay=0.2)
        available = []
        if data and isinstance(data, list):
            for place in data:
                if place.get("status") == "AVAILABLE":
                    price_info = place.get("price") or {}
                    pid = str(price_info.get("id"))
                    if allowed_price_ids and pid not in allowed_price_ids:
                        continue
                    available.append({
                        "id": str(place.get("id")),
                        "price_id": pid,
                        "price": price_info.get("value"),
                        "address": place.get("address", ""),
                    })
        return available

    async def reserve_place(self, place_id: str) -> bool:
        url = (
            f"https://widget.ticketpro.by/ticketpro-api/site/v2/ticket-reserve-by-prototype/{self.event_id}"
            f"?prototype_id={place_id}&count=1&session_id={self.session_id or ''}&widget_id={self.widget_id}"
        )
        data = await curl_request_json(url, headers=self.get_headers(), max_retries=5)
        if data and isinstance(data, dict):
            if data.get("session_id") or data.get("basket_entities") is not None:
                return True
        return False

    async def create_order_stranger(
        self,
        first_name: str,
        last_name: str,
        phone: str,
        email: str,
    ) -> tuple[bool, str | None, dict[str, Any]]:
        clean_phone = re.sub(r"\D", "", phone)
        url = (
            f"https://widget.ticketpro.by/ticketpro-api/site/v2/order-create-as-stranger"
            f"?delivery_method_id=1"
            f"&payment_method_id=3"
            f"&phone={clean_phone}"
            f"&email={email}"
            f"&first_name={first_name}"
            f"&last_name={last_name}"
            f"&country_name=Беларусь"
            f"&region_name=Минская+область"
            f"&city_name=Минск"
            f"&session_id={self.session_id or ''}"
            f"&widget_id={self.widget_id}"
        )
        data = await curl_request_json(url, headers=self.get_headers(), max_retries=15)
        if data and isinstance(data, dict):
            order_id = data.get("id") or (data.get("data", {}).get("id") if isinstance(data.get("data"), dict) else None)
            if order_id:
                return True, str(order_id), data
        return False, None, data or {}


# ==========================================
# 3. Хардкод-функция классического оформления
# ==========================================

def get_order_number(html_content: str) -> str | None:
    pattern = r"(?:Номер заказа|Order number)\s*[:#-]?\s*(\d+)"
    match = re.search(pattern, html_content, re.IGNORECASE | re.UNICODE)
    if match:
        return match.group(1)
    return None


async def complete_guest_erip_checkout(
    cookies: dict[str, str],
    first_name: str,
    last_name: str,
    phone: str,
    email: str,
    base_url: str = "https://www.ticketpro.by",
    max_retries: int = 15,
) -> tuple[bool, str | None, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    async with httpx.AsyncClient(base_url=base_url, headers=headers, cookies=cookies, follow_redirects=True, timeout=35.0) as client:
        # Step 1: GET /order/auth/
        r_auth_get = await client.get("/order/auth/")
        csrf_auth_m = re.search(r'name="_csrf-frontend"\s+value="(.*?)"', r_auth_get.text)
        auth_csrf = csrf_auth_m.group(1) if csrf_auth_m else ""

        # Step 2: POST /order/auth/
        guest_payload = {
            "_csrf-frontend": auth_csrf,
            "form": "guest",
            "first_name": first_name,
            "last_name": last_name,
            "phone": phone,
            "email": email,
            "confirmEmail": email,
        }
        await client.post("/order/auth/", data=guest_payload)

        # Step 3: GET /order/payment/
        r_pay_get = await client.get("/order/payment/")
        csrf_pay_m = re.search(r'name="_csrf-frontend"\s+value="(.*?)"', r_pay_get.text)
        pay_csrf = csrf_pay_m.group(1) if csrf_pay_m else auth_csrf

        # Step 4: POST /order/payment/
        payment_payload = {
            "_csrf-frontend": pay_csrf,
            "delivery": "1",
            "payment": "3",
            "license": "1",
            "privacyPolicy": "1",
            "subscribe": "0",
        }
        r_pay_post = await client.post("/order/payment/", data=payment_payload)
        order_num = get_order_number(r_pay_post.text)
        return True, order_num, r_pay_post.text


# ==========================================
# 4. FSM Состояния
# ==========================================

class SniperStates(StatesGroup):
    waiting_for_event_id = State()
    choosing_price = State()
    choosing_count = State()
    sniping = State()
    waiting_for_user_data = State()


router = Router()
user_active_widget_clients: dict[int, WidgetClient] = {}
user_active_sessions: dict[int, BotSession] = {}


# ==========================================
# 5. Обработчики Telegram
# ==========================================

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏒 Динамо-Минск vs Торпедо (49436)", callback_data="event_widget_49436_9")],
            [InlineKeyboardButton(text="🎯 Максим Фадеев (47425)", callback_data="event_classic_47425")],
            [InlineKeyboardButton(text="✏️ Ввести другой ID или ссылку", callback_data="enter_custom_event")],
        ]
    )
    await message.answer(
        "👋 **Добро пожаловать в Ticketpro Sniper Bot!**\n\n"
        "Поддерживает как классический сайт `ticketpro.by`, так и новый виджет `widget.ticketpro.by`.\n\n"
        "Выберите мероприятие или введите ссылку:",
        reply_markup=kb,
        parse_mode="Markdown",
    )


@router.callback_query(F.data.startswith("event_widget_"))
async def handle_widget_event_selected(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split("_")
    event_id = parts[2]
    widget_id = parts[3] if len(parts) > 3 else "9"
    await callback.answer()
    await load_widget_event_and_ask_price(callback.message, event_id, widget_id, state)


@router.callback_query(F.data.startswith("event_classic_"))
async def handle_classic_event_selected(callback: CallbackQuery, state: FSMContext) -> None:
    event_id = callback.data.replace("event_classic_", "").strip()
    await callback.answer()
    await load_classic_event_and_ask_price(callback.message, event_id, state)


@router.callback_query(F.data == "enter_custom_event")
async def handle_custom_event_request(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(SniperStates.waiting_for_event_id)
    await callback.message.answer(
        "✍️ Отправьте **ссылку** на мероприятие (например `https://widget.ticketpro.by/event/49436?widget_id=9` или `https://www.ticketpro.by/kupit-bilet/47425/`) либо числовой ID:",
        parse_mode="Markdown",
    )


@router.message(SniperStates.waiting_for_event_id)
async def handle_custom_event_id(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()

    # Проверка на виджет-ссылку
    if "widget.ticketpro.by" in text or "widget_id=" in text:
        eid_m = re.search(r"/event/(\d+)", text) or re.search(r"(\d{4,6})", text)
        wid_m = re.search(r"widget_id=(\d+)", text)
        event_id = eid_m.group(1) if eid_m else "49436"
        widget_id = wid_m.group(1) if wid_m else "9"
        await load_widget_event_and_ask_price(message, event_id, widget_id, state)
        return

    # Классический ID
    match = re.search(r"(\d{4,6})", text)
    if not match:
        await message.answer("❌ Не удалось распознать ID мероприятия. Отправьте числовой ID или полную ссылку:")
        return

    event_id = match.group(1)
    await load_classic_event_and_ask_price(message, event_id, state)


# --- Загрузка Виджета ---
async def load_widget_event_and_ask_price(message: Message, event_id: str, widget_id: str, state: FSMContext) -> None:
    wait_msg = await message.answer(f"⏳ Загружаю данные события #{event_id} из виджета Ticketpro...")
    client = WidgetClient(event_id=event_id, widget_id=widget_id)
    event_name, prices = await client.load_event_and_prices()

    if not prices and not client.session_id:
        await wait_msg.edit_text(f"❌ Не удалось загрузить событие #{event_id} из виджета. Проверьте ID.")
        return

    await state.update_data(
        is_widget=True,
        event_id=event_id,
        widget_id=widget_id,
        event_name=event_name,
        prices=prices,
    )
    user_active_widget_clients[message.chat.id] = client

    buttons = []
    if prices:
        for pid, pdata in sorted(prices.items(), key=lambda x: x[1].get("price", 0)):
            p_val = pdata.get("price", 0)
            buttons.append([InlineKeyboardButton(
                text=f"🎫 {p_val:.2f} BYN (ID: {pid})",
                callback_data=f"price_{pid}",
            )])
    buttons.append([InlineKeyboardButton(text="🎯 Любая цена (все категории)", callback_data="price_any")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await state.set_state(SniperStates.choosing_price)
    await wait_msg.edit_text(
        f"🎟 **Мероприятие:** {event_name}\n"
        f"⚡ **Режим:** Виджет (widget.ticketpro.by)\n\n"
        f"Выберите желаемую стоимость билета:",
        reply_markup=kb,
        parse_mode="Markdown",
    )


# --- Загрузка Классического сайта ---
async def load_classic_event_and_ask_price(message: Message, event_id: str, state: FSMContext) -> None:
    wait_msg = await message.answer(f"⏳ Загружаю данные мероприятия #{event_id}...")

    fetcher = Fetcher(event_id=event_id)
    page_res = None
    for attempt in range(1, 4):
        page_res = await fetcher.fetch_page()
        if page_res:
            break
        await asyncio.sleep(0.5)

    if not page_res:
        # Автоматический fallback на виджет при 502/ошибке сайта
        logger.info(f"[TG Bot] Сайт ticketpro.by недоступен для #{event_id}, переключаюсь на виджет...")
        await wait_msg.edit_text(f"🔄 Страница недоступна на основном сайте, пробую загрузить через виджет Ticketpro...")
        await load_widget_event_and_ask_price(message, event_id, "9", state)
        return

    html_text, cookies, csrf_token = page_res
    prices = CurrentTicketproParser.extract_event_prices(html_text)

    name_m = re.search(r"<h1\b[^>]*>(.*?)</h1>", html_text, re.DOTALL)
    event_name = re.sub(r"<[^>]+>", "", name_m.group(1)).strip() if name_m else f"Событие #{event_id}"

    await bot_manager.prepare_presession(
        event_id=event_id,
        html_text=html_text,
        cookies=cookies,
        event_name=event_name,
    )

    await state.update_data(
        is_widget=False,
        event_id=event_id,
        event_name=event_name,
        prices=prices,
        cookies=cookies,
        csrf_token=csrf_token,
    )

    buttons = []
    if prices:
        for pid, pdata in sorted(prices.items(), key=lambda x: x[1].get("price", 0)):
            p_val = pdata.get("price", 0)
            buttons.append([InlineKeyboardButton(
                text=f"🎫 {p_val:.2f} BYN (ID: {pid})",
                callback_data=f"price_{pid}",
            )])
    buttons.append([InlineKeyboardButton(text="🎯 Любая цена (все категории)", callback_data="price_any")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await state.set_state(SniperStates.choosing_price)
    await wait_msg.edit_text(
        f"🎟 **Мероприятие:** {event_name}\n\n"
        f"Выберите желаемую стоимость билета:",
        reply_markup=kb,
        parse_mode="Markdown",
    )


@router.callback_query(SniperStates.choosing_price, F.data.startswith("price_"))
async def handle_price_chosen(callback: CallbackQuery, state: FSMContext) -> None:
    price_choice = callback.data.replace("price_", "")
    await callback.answer()

    data = await state.get_data()
    selected_price_id = None if price_choice == "any" else price_choice
    await state.update_data(selected_price_id=selected_price_id)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="1 шт", callback_data="count_1"),
                InlineKeyboardButton(text="2 шт", callback_data="count_2"),
            ],
            [
                InlineKeyboardButton(text="3 шт", callback_data="count_3"),
                InlineKeyboardButton(text="4 шт", callback_data="count_4"),
            ],
        ]
    )
    await state.set_state(SniperStates.choosing_count)

    price_label = "Любая" if not selected_price_id else f"{data['prices'][selected_price_id]['price']} BYN"
    await callback.message.edit_text(
        f"✅ Выбранная цена: **{price_label}**\n\n"
        f"Сколько билетов необходимо поймать?",
        reply_markup=kb,
        parse_mode="Markdown",
    )


@router.callback_query(SniperStates.choosing_count, F.data.startswith("count_"))
async def handle_count_chosen(callback: CallbackQuery, state: FSMContext) -> None:
    count = int(callback.data.replace("count_", ""))
    await callback.answer()

    data = await state.get_data()
    is_widget = data.get("is_widget", False)
    event_id = data["event_id"]
    event_name = data["event_name"]
    selected_price_id = data.get("selected_price_id")
    allowed_price_ids = {selected_price_id} if selected_price_id else None

    await state.set_state(SniperStates.sniping)
    await callback.message.edit_text(
        f"🎯 **Снайпер успешно запущен!**\n\n"
        f"• **Событие:** {event_name}\n"
        f"• **Цель:** {count} билет(а)\n"
        f"• **Категория цены:** {'Любая' if not selected_price_id else selected_price_id}\n\n"
        f"⏳ Ожидаем появление свободных мест...",
        parse_mode="Markdown",
    )

    if is_widget:
        client = user_active_widget_clients.get(callback.message.chat.id) or WidgetClient(event_id, data.get("widget_id", "9"))
        user_active_widget_clients[callback.message.chat.id] = client
        asyncio.create_task(
            monitor_widget_hunt(
                bot=callback.bot,
                chat_id=callback.message.chat.id,
                client=client,
                target_count=count,
                allowed_price_ids=allowed_price_ids,
                state=state,
            )
        )
    else:
        req = StartBotRequest(
            event_id=event_id,
            target_tickets=count,
            allowed_price_ids=list(allowed_price_ids) if allowed_price_ids else None,
            num_consumers=5,
            poll_interval=0.5,
        )
        try:
            session = await bot_manager.start_session(req=req, cookies=data.get("cookies", {}))
            user_active_sessions[callback.from_user.id] = session
            asyncio.create_task(
                monitor_classic_hunt(
                    bot=callback.bot,
                    chat_id=callback.message.chat.id,
                    user_id=callback.from_user.id,
                    session=session,
                    state=state,
                )
            )
        except Exception as e:
            await callback.message.edit_text(f"❌ Ошибка старта: {e}")


# --- Фоновая охота в Виджете ---
async def monitor_widget_hunt(
    bot: Bot,
    chat_id: int,
    client: WidgetClient,
    target_count: int,
    allowed_price_ids: set[str] | None,
    state: FSMContext,
) -> None:
    client.is_hunting = True
    booked_places: list[dict[str, Any]] = []
    attempted_places: set[str] = set()

    logger.info(
        f"[WidgetSniper] 🎯 Старт охоты за билетами #{client.event_id} ({client.event_name}) | "
        f"Цель: {target_count} | Категории цен: {allowed_price_ids or 'Любая'}"
    )

    iteration = 0
    while client.is_hunting and len(booked_places) < target_count:
        iteration += 1
        logger.info(
            f"[WidgetSniper] Итерация #{iteration} (Забронировано: {len(booked_places)}/{target_count}) — "
            f"опрос схемы мест #{client.event_id}..."
        )
        places = await client.fetch_available_places(allowed_price_ids=allowed_price_ids)
        if places:
            logger.info(f"[WidgetSniper] 🔥 Обнаружено {len(places)} свободных мест на итерации #{iteration}!")
            for place in places:
                pid = place["id"]
                if pid not in attempted_places:
                    attempted_places.add(pid)
                    logger.info(f"[WidgetSniper] Бронирую место {place['address']} (ID: {pid}, цена: {place.get('price')} BYN)...")
                    ok = await client.reserve_place(pid)
                    if ok:
                        booked_places.append(place)
                        logger.info(
                            f"[WidgetSniper] 🎉 Успешно забронировано место {place['address']} (ID: {pid})! "
                            f"Прогресс: {len(booked_places)}/{target_count}"
                        )
                        if len(booked_places) >= target_count:
                            break
                    else:
                        logger.warning(f"[WidgetSniper] Не удалось забронировать {pid} (возможно, уже перехвачено).")

        await asyncio.sleep(0.5)

    if booked_places:
        booked_text = "\n".join([f"• {p['address']} ({p.get('price')} BYN)" for p in booked_places])
        await state.set_state(SniperStates.waiting_for_user_data)
        await state.update_data(
            booked_places=booked_places,
            is_widget=True,
        )

        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"🎉 **Бот заснайпил места:**\n{booked_text}\n\n"
                f"📝 **Отправьте данные для авторизации и оформления:**\n\n"
                f"Формат в одну строку:\n"
                f"`Имя Фамилия +375291234567 email@example.com`\n\n"
                f"_Бронь действительна 10 минут!_"
            ),
            parse_mode="Markdown",
        )
    else:
        await bot.send_message(chat_id=chat_id, text="⏹ Снайпер был остановлен.")


# --- Фоновая охота Classic ---
async def monitor_classic_hunt(bot: Bot, chat_id: int, user_id: int, session: BotSession, state: FSMContext) -> None:
    if session.task:
        try:
            await session.task
        except Exception as e:
            logger.error(f"[ClassicSniper] Session error: {e}")

    if session.booked_items:
        booked_text = "\n".join([f"• Билет #{item.get('ticket_id')} (Категория: {item.get('price_id')})" for item in session.booked_items])
        await state.set_state(SniperStates.waiting_for_user_data)
        await state.update_data(
            session_cookies=session.ctx.cookies,
            booked_items=session.booked_items,
            is_widget=False,
        )
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"🎉 **Бот заснайпил места:**\n{booked_text}\n\n"
                f"📝 **Отправьте данные для авторизации и оформления:**\n\n"
                f"Формат в одну строку:\n"
                f"`Имя Фамилия +375291234567 email@example.com`"
            ),
            parse_mode="Markdown",
        )


@router.message(SniperStates.waiting_for_user_data)
async def handle_user_contact_data(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    parts = text.split()
    if len(parts) < 4:
        parts = [p.strip() for p in text.split("\n") if p.strip()]

    if len(parts) < 4:
        await message.answer(
            "⚠️ Пожалуйста, укажите все 4 поля через пробел или с новой строки:\n"
            "`Имя Фамилия НомерТелефона Email`\n"
            "Пример:\n`Иван Иванов +375291112233 ivan@gmail.com`",
            parse_mode="Markdown",
        )
        return

    first_name, last_name, phone, email = parts[0], parts[1], parts[2], parts[3]
    data = await state.get_data()
    is_widget = data.get("is_widget", False)

    status_msg = await message.answer("⏳ Регистрирую заказ и выставляю счет в ЕРИП...")

    if is_widget:
        client = user_active_widget_clients.get(message.chat.id)
        if not client:
            client = WidgetClient(data.get("event_id", "49436"), data.get("widget_id", "9"))

        ok, order_id, raw_resp = await client.create_order_stranger(
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            email=email,
        )

        if ok and order_id:
            await status_msg.edit_text(
                f"✅ **Заказ успешно оформлен!**\n\n"
                f"📋 **Номер заказа:** `{order_id}`\n"
                f"👤 **Покупатель:** {first_name} {last_name}\n"
                f"📱 **Телефон:** `{phone}`\n"
                f"📧 **Email для билетов:** `{email}`\n"
                f"💳 **Способ оплаты:** ЕРИП (Система «Расчет»)\n\n"
                f"📌 **Инструкция по оплате в ЕРИП:**\n"
                f"1. Откройте интернет-банкинг или мобильный банк\n"
                f"2. Перейдите: `Система \"Расчет\" (ЕРИП)` -> `Билеты, лотереи` -> `Билетные операторы` -> `Ticketpro.by` -> `Оплата билетов`\n"
                f"3. Введите номер заказа: `{order_id}`\n"
                f"4. Проверьте сумму и оплатите счет.\n\n"
                f"Билеты придут на почту `{email}` сразу после оплаты!",
                parse_mode="Markdown",
            )
            await state.clear()
            return
        else:
            await status_msg.edit_text(
                f"⚠️ Ответ сервера: {raw_resp}\n\nПроверьте почту `{email}` — возможно, заказ уже зарегистрирован.",
                parse_mode="Markdown",
            )
            await state.clear()
            return

    # Classic Checkout
    cookies = data.get("session_cookies", {})
    ok, order_number, raw_resp = await complete_guest_erip_checkout(
        cookies=cookies,
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        email=email,
    )

    if ok and order_number:
        await status_msg.edit_text(
            f"✅ **Заказ успешно оформлен!**\n\n"
            f"📋 **Номер заказа:** `{order_number}`\n"
            f"💳 **Способ оплаты:** ЕРИП (Система «Расчет»)\n"
            f"📧 Билеты будут высланы на `{email}`!",
            parse_mode="Markdown",
        )
        await state.clear()
    else:
        await status_msg.edit_text(f"⚠️ Не удалось извлечь номер заказа. Проверьте почту `{email}`.", parse_mode="Markdown")
        await state.clear()


@router.message(Command("stop"))
async def cmd_stop(message: Message, state: FSMContext) -> None:
    # Остановка виджета
    w_client = user_active_widget_clients.pop(message.chat.id, None)
    if w_client:
        w_client.is_hunting = False

    # Остановка classic
    session = user_active_sessions.pop(message.from_user.id, None)
    if session and session.is_running():
        session.stop()

    await state.clear()
    await message.answer("⏹ Снайпер успешно остановлен.")


# ==========================================
# 6. Главная точка входа
# ==========================================

async def main() -> None:
    token = BOT_TOKEN.strip() if BOT_TOKEN else (os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN"))
    if not token:
        if len(sys.argv) > 1:
            token = sys.argv[1]
        else:
            logger.error("❌ Не указан токен Telegram-бота в BOT_TOKEN!")
            sys.exit(1)

    bot = Bot(token=token)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    logger.info("🚀 Telegram Sniper Bot (Classic + Widget) успешно запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
