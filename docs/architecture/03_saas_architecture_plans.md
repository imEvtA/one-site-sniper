# Архитектура и спецификация SaaS-платформы (Multi-User)

> **Статус документа:** 🟡 `[Active Design Phase]`  
> **Контекст:** Проектирование модульной SaaS-архитектуры с независимыми слоями (Bot Engine, Service Layer, Interfaces Layer).

---

## 1. Реестр модулей и зон ответственности

```
                        ┌────────────────────────────────────────────────────────┐
                        │                   1. INTERFACES LAYER                  │
                        │    • Web HUD Overlay (DOM-инъекция)                    │
                        │    • REST & SSE API (FastAPI)                          │
                        │    • Telegram Bot Daemon (aiogram / webhook)           │
                        └─────────────┬────────────────────────────▲─────────────┘
                         Запросы/DTO  │                            │ События / Push
                                      ▼                            │
┌──────────────────────────────────────────────────────────────────┴───────────────────────────────┐
│                                       2. SAAS / SERVICE LAYER                                    │
│                                                                                                  │
│   ┌───────────────────────────┐    ┌───────────────────────────┐    ┌─────────────────────────┐  │
│   │       GatewayManager      │    │     AllocationManager     │    │   NotificationManager   │  │
│   │  • Middleware пайплайн    │    │  • Диспетчер билетов      │    │  • Event-Driven Bus     │  │
│   │  • HMAC Identity & Auth   │    │  • Сопоставление с RAM    │    │  • Push в интерфейсы    │  │
│   │  • Rate-Limiting / WAF    │    │  • Приоритеты и лимиты    │    │  • Zero-delay доставка  │  │
│   └─────────────┬─────────────┘    └─────────────▲─────────────┘    └────────────▲────────────┘  │
│                 │                                │                               │               │
│                 │                                ├───────────────────────────────┘               │
│                 ▼                                ▼                                               │
│   ┌──────────────────────────────────────────────────────────────────────────────────────────┐  │
│   │                        StorageOrchestrator (Оркестратор хранилища)                       │  │
│   │   • Репозитории данных (Users, Tasks, Bookings)                                          │  │
│   │   • In-Memory RAM Cache активных задач                                                   │  │
│   │   • Асинхронный SQLite WAL / пул соединений с автовосстановлением                        │  │
│   └──────────────────────────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────▲───────────────────────────────────────────────┘
                                                   │ BookedTicketPayload (ticket, price, cookies)
                                                   │
                        ┌──────────────────────────┴────────────────────────────┐
                        │                     3. BOT ENGINE                     │
                        │  • ProducerUnit (1 Fetcher + 1 Parser)                │
                        │  • Multi-Producer Support (Staggered Polling)         │
                        │  • Zero-Lock Atomic Filter Snapshots                  │
                        │  • ConsumerPool (изолированные сессии воркеров)       │
                        └───────────────────────────────────────────────────────┘
```

---

## 2. Принятые архитектурные решения (Decision Log / Approved ADRs)

> **Примечание:** Все решения в этом логе имеют статус `[APPROVED]` (утверждены в дизайне). Статус физической реализации отслеживается в [`SAAS_IMPLEMENTATION_TRACKING.md`](file:///home/imevt/imEvt/projects/ticketpro/docs/SAAS_IMPLEMENTATION_TRACKING.md).

- **ADR-01: [APPROVED] Трёхуровневая Clean Architecture**
  - Полное разделение `Bot Engine`, `Service Layer` и `Interfaces Layer`. Слои общаются строго через DTO/Payload и Event Bus.
- [x] **ADR-02: Инкапсуляция ProducerUnit в Bot Engine**
  - Связка `1 Fetcher + 1 Parser` оформляется как единый `ProducerUnit`. Это позволяет масштабировать мониторинг схемы на `N` параллельных продюсеров со сдвигом по времени (Staggered Polling).
- [x] **ADR-03: Zero-Lock динамические фильтры спроса (Atomic Reference Swap)**
  - Вместо блокировок `asyncio.Lock` при добавлении/удалении пользовательских фильтров используется атомарная замена ссылки на иммутабельный снимок фильтров `FilterSnapshot`. Допустим минимальный процент ложных срабатываний/пропусков на стыке тиков, что нивелирует накладные расходы на синхронизацию.
- [x] **ADR-04: Отказ от слепого снайпинга (No-Filter Spoilage)**
  - Бот не бронирует случайные билеты без активного спроса пользователей, чтобы исключить подозрительный спам отменами броней в адрес Ticketpro.
- [x] **ADR-05: Гранулярная модель корзин и границы ответственности чекаута**
  - `Bot Engine` поддерживает оба режима (поштучный и пакетный).
  - В `SaaS Service Layer` базовая модель — **поштучная изоляция** (1 пойманное место = 1 независимая бронь с уникальными куками). Задача интерфейса на `N` пришедших от сервера куки агрегировать список из `N` независимых броней (`list[Booking]`) и красиво показывать пользователю.
  - **Граница ответственности:** Ни `Bot Engine`, ни `SaaS Service Layer` не занимаются оплатой, заполнением персональных данных или регистрацией на Ticketpro. Переключение между корзинами, отображение ссылок на оплату или авто-чекаут — это исключительная зона ответственности конкретного интерфейса (`Web HUD` / `TG Bot`).
- [x] **ADR-06: Platform-Agnostic Bot Engine (Универсальный движок снайпинга)**
  - Оркестраторы ядра (`BotSession`, `ProducerPool`, `ConsumerPool`) **не содержат специфики конкретного сайта** (никаких захардкоженных URL, regex, заголовков).
  - Вся специфика целевой площадки инкапсулируется в абстрактных стратегиях: `BaseFetcher`, `BaseParser`, `BaseConsumer`, `BaseSessionProvider`. Переключение на другой билетный оператор сводится к подмене этих классов без изменения ядра.
- [x] **ADR-07: Выделенный SessionProvider / CookieManager**
  - Консьюмер (`Consumer`) не умеет сам ходить за куками и не знает о `Fetcher`. Его зона ответственности — только отправка POST брони.
  - Управление пулом сессий вынесено в отдельный `SessionProvider` (или `CookieManager`). Консьюмер лишь запрашивает готовую сессию из провайдера, а после успешной брони или инвалидации сигнализирует провайдеру о необходимости выдать новую.
- [x] **ADR-08: Стратегии распределения билетов (BaseAllocationStrategy)**
  - `AllocationManager` использует паттерн «Стратегия» (`BaseAllocationStrategy`).
  - **Дефолтная стратегия — `FairShareAllocationStrategy` (Честное распределение / Round-Robin):** балансирует выдачу так, чтобы каждый пользователь сначала получил по 1 билету, исключая монополизацию снайпера одним пользователем с большим `target_tickets`.
  - Альтернативные стратегии (`FifoAllocationStrategy`, `SpecificityAllocationStrategy`) подключаются через единый интерфейс без изменения логики менеджера.
- [x] **ADR-09: Изолированный Event-Driven Bus и персистентность по таймеру жизни (TTL)**
  - SaaS-сервер полностью абстрагирован от интерфейсов. При аллокации билета сервис выполняет два действия:
    1. Асинхронно сохраняет бронь в БД со статусом `unclaimed` и временем жизни `expires_at` (ровно 10 минут). Бронь активна в БД строго до истечения этого таймера независимо от онлайна/офлайна юзера.
    2. Триггерит реактивное событие `BookingAllocatedEvent(user_id, booking_id, expires_at)` в `NotificationManager`.
  - Любой подключенный интерфейс (SSE-хэндлер веба, Telegram-бот) слушает шину и реагирует на событие по-своему. Если пользователь был офлайн или перезагрузил страницу, интерфейс восстанавливает состояние, запрашивая активные неистекшие брони из БД через REST API.
- [x] **ADR-10: Структура ответа клейма корзины (Atomic Booking Payload)**
  - Эндпоинты забора/списка броней возвращают список независимых объектов `list[BookingItem]`. Каждый объект содержит `booking_id`, `ticket_id`, `seat_info`, `price`, `expires_at`, `time_left_sec` и изолированный словарь `cookies`.
  - Это исключает рассинхронизацию кук, даёт богатый контекст любому UI (HUD / Telegram) и поддерживает частичный клейм или отправку ссылок друзьям.
- [x] **ADR-11: Универсальная авторизация через BaseTokenHandler**
  - GatewayManager принимает инвариант `(token_type: str, raw_token: str)`.
  - Интерпретация, валидация и генерация токенов делегируется стратегии `BaseTokenHandler`:
    - `HmacCookieTokenHandler` — криптографически подписанные анонимные куки для браузерного веба (`HMAC-SHA256`).
    - `ServiceSecretTokenHandler` / `TelegramTokenHandler` — служебные токены для Telegram-демона или микросервисов.
    - `JwtBearerTokenHandler` — для классических JWT при интеграции с внешними системами.
- [x] **ADR-12: Ролевая модель и динамические лимиты (RBAC)**
  - Пользователю присваивается роль (`UserRole: GUEST, USER, VIP, ADMIN`).
  - Лимиты на максимальное количество одновременно активных задач (`max_active_tasks`) и доступ к закрытым/эксклюзивным событиям валидируются в middleware на основе роли.
- [x] **ADR-13: Универсальный 64-битный пространственный лейаут (Spatial Bit-Packing)**
  - Фильтр мест и цен упаковывается ровно в один 64-битный `int` (`uint64`):
    - **16 бит:** `location_id` (зона / трибуна / сектор, 0..65535, где 0 = Any).
    - **8 бит:** `row_start` (0..255).
    - **8 бит:** `row_count` (0..255, где 0 = All rows).
    - **8 бит:** `seat_start` (0..255).
    - **8 бит:** `seat_count` (0..255, где 0 = All seats).
    - **16 бит:** `price_mask` (битовая маска до 16 ценовых категорий, где 0 = All prices).
  - Проверка билета на соответствие фильтру выполняется через побитовые сдвиги и маски за 1 такт процессора. Сложные составные зоны хранятся как `list[uint64]`.
- [x] **ADR-14: Чистый Python EventBus (Zero-Broker Architecture)**
  - Шина событий `BaseEventBus` реализуется на чистом Python (`InMemoryEventBus` на базе `asyncio.Queue`).
  - Обеспечивает задержку 0.001 мс, нулевые накладные расходы по памяти и отсутствие внешних зависимостей (Kafka/Redis). При необходимости перехода на распределенный кластер создается адаптер `RedisEventBus` без изменения бизнес-логики.
- [x] **ADR-15: Стек хранилища, Crash Recovery и авто-очистка TTL**
  - **Data Layer:** `SQLAlchemy 2.0 Async` + `aiosqlite` (WAL режим) / `asyncpg` с паттерном Repository внутри `StorageOrchestrator`.
  - **Crash Recovery:** При старте сервера в `lifespan` автоматически пересчитываются активные задачи и возобновляются `BotSession`.
  - **Periodic TTL Sweep:** Фоновая корутина `_expiry_cleanup_loop` каждые 30 секунд помечает брони с `expires_at < now` как `expired` и корректирует счетчик задачи.

---

## 3. Очередь вопросов для проектирования (Roadmap Questions)

### Тема 1: Bot Engine & Consumer Lifecycle
- [x] ~~**1.1. Агрегация фильтров и модель продюсера.**~~
- [x] ~~**1.2. Стратегия корзин:** Поштучная ловля (1 билет = 1 сессия) vs Пакетная ловля (N билетов в 1 сессию).~~
- [x] ~~**1.3. Жизненный цикл сессий воркеров:** Выделенный SessionProvider и изоляция консьюмеров.~~

### Тема 2: Allocation & Notification Engine
- [x] ~~**2.1. Алгоритм распределения (Matcher Algorithm):** Приоритеты и BaseAllocationStrategy.~~
- [x] ~~**2.2. Event Bus & Уведомления:** Изолированный Event Bus и персистентность по таймеру жизни.~~

### Тема 3: Interfaces & Hand-off Flow
- [x] ~~**3.1. Basket Hand-off:** Процесс передачи корзины в браузер и Telegram (Atomic Booking Payload).~~
- [x] ~~**3.2. Gateway Middleware:** Базовый обработчик токенов BaseTokenHandler и ролевые лимиты RBAC.~~

### Тема 4: Storage Orchestration & Схема БД (Финальный этап)
- [x] ~~**4.1. Проектирование структуры таблиц, индексов и пространственного кодирования.**~~
- [x] ~~**4.2. Механизмы восстановления состояния (Crash Recovery & Expiry Cleanup).**~~

---

## 4. Детализированная спецификация

### 4.1. Схема базы данных (DDL)

```sql
-- 1. Роли и системные лимиты
CREATE TABLE roles (
    name TEXT PRIMARY KEY,                     -- 'GUEST', 'USER', 'VIP', 'ADMIN'
    max_active_tasks INTEGER NOT NULL DEFAULT 1,
    priority_level INTEGER NOT NULL DEFAULT 0,
    can_target_exclusive BOOLEAN NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Пользователи
CREATE TABLE users (
    id TEXT PRIMARY KEY,                       -- UUID
    role_name TEXT NOT NULL REFERENCES roles(name) DEFAULT 'GUEST',
    external_id TEXT UNIQUE,                   -- telegram:12345678 или web_fingerprint
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_users_external ON users(external_id);

-- 3. Задачи с фильтрами
CREATE TABLE user_tasks (
    id TEXT PRIMARY KEY,                       -- UUID
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL,
    target_tickets INTEGER NOT NULL DEFAULT 1,
    booked_count INTEGER NOT NULL DEFAULT 0,
    filter_boxes TEXT NOT NULL,                -- JSON-массив uint64: "[844424930131970, ...]"
    status TEXT NOT NULL DEFAULT 'active',     -- 'active', 'completed', 'paused', 'cancelled'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_tasks_event_active ON user_tasks(event_id, status);
CREATE INDEX idx_tasks_user ON user_tasks(user_id);

-- 4. Брони (10 минут TTL)
CREATE TABLE bookings (
    id TEXT PRIMARY KEY,                       -- UUID
    task_id TEXT NOT NULL REFERENCES user_tasks(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL,
    ticket_id TEXT NOT NULL,
    price_id TEXT NOT NULL,
    seat_info TEXT,
    price_value REAL NOT NULL,
    session_cookies TEXT NOT NULL,             -- JSON: {"PHPBACKSESSID": "...", "_csrf-frontend": "..."}
    status TEXT NOT NULL DEFAULT 'unclaimed',  -- 'unclaimed', 'claimed', 'expired'
    booked_at TIMESTAMP NOT NULL,              -- Точный timestamp ответа Ticketpro
    expires_at TIMESTAMP NOT NULL,             -- booked_at + 600 сек (10 мин)
    claimed_at TIMESTAMP
);
CREATE INDEX idx_bookings_user_active ON bookings(user_id, status);
CREATE INDEX idx_bookings_expires ON bookings(expires_at, status);
```

### 4.2. Контракты DTO и сущностей

```python
@dataclass(slots=True, frozen=True)
class BookedTicketPayload:
    event_id: str
    ticket_id: str
    price_id: str
    price_value: float
    seat_info: str
    session_cookies: dict[str, str]
    booked_at: float
    expires_at: float

@dataclass(slots=True, frozen=True)
class SpatialBox:
    location_id: int = 0      # 16 бит: 0 = Any
    row_start: int = 0        # 8 бит
    row_count: int = 0        # 8 бит: 0 = All rows
    seat_start: int = 0       # 8 бит
    seat_count: int = 0       # 8 бит: 0 = All seats
    price_mask: int = 0       # 16 бит: 0 = All prices

@dataclass(slots=True, frozen=True)
class BookingItem:
    booking_id: str
    ticket_id: str
    seat_info: str
    price: float
    expires_at: float
    time_left_sec: int
    cookies: dict[str, str]
```
