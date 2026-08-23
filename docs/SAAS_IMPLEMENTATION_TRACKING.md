# SaaS Architecture Implementation Tracking

> **Статус:** 🚀 `[In Progress]`  
> **Архитектурная спецификация:** [`docs/architecture/03_saas_architecture_plans.md`](file:///home/imevt/imEvt/projects/ticketpro/docs/architecture/03_saas_architecture_plans.md)

---

## 📋 Дорожная карта и прогресс реализации

### 🔹 Этап 1: Пространственное кодирование и Domain-слой (`core/spatial/`)
- [x] **1.1. Базовые контракты:** [`core/spatial/base.py`](file:///home/imevt/imEvt/projects/ticketpro/core/spatial/base.py) — `SpatialBox` (16 бит location, 8 бит row/seat start/count, 16 бит price_mask), интерфейс `BaseSpatialEncoder`.
- [x] **1.2. 64-битный упаковщик:** [`core/spatial/bit_packer.py`](file:///home/imevt/imEvt/projects/ticketpro/core/spatial/bit_packer.py) — побитовая упаковка/распаковка в `uint64` и наносекундный матчер `is_match()`.
- [x] **1.3. Unit-тесты:** [`tests/test_spatial.py`](file:///home/imevt/imEvt/projects/ticketpro/tests/test_spatial.py) — 100% покрытие битовых сдвигов, граничных значений и wildcard (нулевых бит) (6 тестов, 0.000s).

---

### 🔹 Этап 2: Универсальный движок добычи (`Bot Engine Refactor`)
- [x] **2.1. Пейлоады ядра:** DTO `BookedTicketPayload`, `FilterSnapshot` в [`core/tasks/payloads.py`](file:///home/imevt/imEvt/projects/ticketpro/core/tasks/payloads.py).
- [x] **2.2. Провайдер сессий:** `BaseSessionProvider` и `TicketproSessionProvider` в [`core/tasks/session_provider.py`](file:///home/imevt/imEvt/projects/ticketpro/core/tasks/session_provider.py).
- [x] **2.3. Модуль продюсера:** `ProducerUnit` (инкапсуляция `1 Fetcher + 1 Parser`) в [`core/tasks/producer.py`](file:///home/imevt/imEvt/projects/ticketpro/core/tasks/producer.py).
- [x] **2.4. Пул консьюмеров:** `ConsumerPool` с забором сессий из `SessionProvider` и эмитом `BookedTicketPayload` в [`core/tasks/consumer.py`](file:///home/imevt/imEvt/projects/ticketpro/core/tasks/consumer.py).
- [x] **2.5. Сессия добытчика:** `BotSession` с поддержкой `Zero-Lock FilterSnapshot` и `ProducerUnit` в [`core/bot.py`](file:///home/imevt/imEvt/projects/ticketpro/core/bot.py).
- [x] **2.6. Unit-тесты ядра:** [`tests/test_engine_refactor.py`](file:///home/imevt/imEvt/projects/ticketpro/tests/test_engine_refactor.py) (39 тестов суммарно, все зеленые).

---

### 🔹 Этап 3: Сервисный слой и шина событий (`Service Layer`)
- [x] **3.1. Event Bus:** `BaseEventBus` и `InMemoryEventBus` (`asyncio.Queue`, zero-latency) в [`saas/service/bus.py`](file:///home/imevt/imEvt/projects/ticketpro/saas/service/bus.py).
- [x] **3.2. Стратегии аллокации:** `BaseAllocationStrategy` и `FairShareAllocationStrategy` (Round-Robin) в [`saas/service/allocation.py`](file:///home/imevt/imEvt/projects/ticketpro/saas/service/allocation.py).
- [x] **3.3. Диспетчер билетов:** `AllocationManager` (RAM-матчинг свободных мест с активными задачами) в [`saas/service/allocation.py`](file:///home/imevt/imEvt/projects/ticketpro/saas/service/allocation.py).
- [x] **3.4. Менеджер уведомлений:** `NotificationManager` (SSE поток W3C и каналы) в [`saas/service/notifications.py`](file:///home/imevt/imEvt/projects/ticketpro/saas/service/notifications.py).
- [x] **3.5. Unit-тесты сервисного слоя:** [`tests/test_service_layer.py`](file:///home/imevt/imEvt/projects/ticketpro/tests/test_service_layer.py) (4 теста, 0.006s).

---

### Этап 4: Персистентное хранилище (Storage Layer) — `[x] ВЫПОЛНЕНО`
- [x] **4.1. Модели SQLAlchemy 2.0 Async (`saas/storage/models.py`)**
  - Таблица `roles` (`name`, `max_active_tasks`, `priority_level`, `can_target_exclusive`, `created_at`).
  - Таблица `users` (`id`, `role_name`, `external_id`, `created_at`).
  - Таблица `user_tasks` (`id`, `user_id`, `event_id`, `target_tickets`, `booked_count`, `filter_boxes_json`, `status`, `created_at`).
  - Таблица `bookings` (`id`, `task_id`, `user_id`, `event_id`, `ticket_id`, `price_id`, `seat_info`, `price_value`, `session_cookies_json`, `status`, `booked_at`, `expires_at`, `claimed_at`, `created_at`).
- [x] **4.2. StorageOrchestrator (`saas/storage/orchestrator.py`)**
  - Поддержка SQLite WAL (`PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;`) и PostgreSQL.
  - CRUD-операции для пользователей, задач и броней.
  - Поштучный забор корзины `claim_booking()` со сменой статуса на `claimed`.
  - Фоновый цикл авто-очистки `_periodic_cleanup` (каждые 30 сек) для перевода протухших броней в `expired` и корректировки счетчиков задач.
  - `recover_on_startup()`: восстановление незавершенных активных задач и авто-инвалидация просроченных броней после рестарта.
- [x] **4.3. Тестирование хранилища (`tests/test_storage.py`)**
  - Покрытие юнит-тестами на in-memory SQLite (4 теста, 100% success).

---

### 🔹 Этап 5: Шлюз и авторизация (`Gateway Layer`) — `[x] ВЫПОЛНЕНО`
- [x] **5.1. Обработчики токенов:** `BaseTokenHandler`, `HmacCookieTokenHandler`, `ServiceSecretTokenHandler`, `CompositeTokenHandler` в [`saas/gateway/auth.py`](file:///home/imevt/imEvt/projects/ticketpro/saas/gateway/auth.py).
- [x] **5.2. SaaS Gateway Orchestrator:** `SaaSGatewayOrchestrator` (мост между БД, Bot Engine, AllocationManager и SSE) в [`saas/gateway/orchestrator.py`](file:///home/imevt/imEvt/projects/ticketpro/saas/gateway/orchestrator.py).
- [x] **5.3. REST & SSE Контроллеры (`saas/gateway/routes.py`):**
  - `POST /api/gateway/auth/guest` (выпуск гостевого токена и установка cookie).
  - `POST /api/gateway/tasks` (создание задачи с пространственными фильтрами `filter_boxes` и проверка лимитов роли).
  - `GET /api/gateway/tasks` (список задач пользователя).
  - `DELETE /api/gateway/tasks/{task_id}` (отмена задачи).
  - `POST /api/gateway/bookings/claim` (забор корзины с возвратом `BookingItem`).
  - `GET /api/gateway/bookings` (активные брони пользователя).
  - `GET /api/gateway/stream` (SSE live-поток событий пользователя).
- [x] **5.4. Тестирование шлюза (`tests/test_gateway.py`):**
  - 4 юнит/интеграционных теста FastAPI + HMAC + RBAC + Multi-User Allocation (100% success).

---

### 🔹 Этап 6: Разделение на два независимых сервиса (`SaaS Server` & `Web Proxy`) — `[x] ВЫПОЛНЕНО`
- [x] **6.1. Выделенный SaaS Core Backend (`saas/server.py` — порт 8001):**
  - Запуск `SaaSGatewayOrchestrator`, SQLAlchemy Async DB, фонового воркера очистки TTL (30с), `AllocationManager`, `InMemoryEventBus` и `BotSession`.
  - Маршруты REST/SSE `/api/gateway/*` с полной поддержкой CORS.
- [x] **6.2. Легковесный Web Proxy & HUD Frontend (`web/server.py` — порт 8000):**
  - Полностью отвязан от ядра ботов и БД (`core.bot` и `bot_manager` удалены из веб-слоя).
  - Реверс-прокси к `ticketpro.by` с инъекцией HUD (`overlay.js`, `overlay.css`).
  - Проксирование запросов шлюза (`/api/gateway/*` → `http://127.0.0.1:8001`).
- [x] **6.3. Обновление HUD интерфейса (`web/static/overlay.js`):**
  - 100% переход на вызовы SaaS Gateway API (`/api/gateway/tasks`, `/api/gateway/bookings/claim`, `/api/gateway/stream`).
  - Live-лента задач и готовых корзин с таймером обратного отсчёта.
- [x] **6.4. Сквозное тестирование:** 51 юнит/интеграционный тест проходит без ошибок (`Ran 51 tests ... OK`).

