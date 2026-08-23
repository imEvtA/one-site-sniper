// Ticketpro SaaS Client HUD - Multi-Tenant Hub & Real-Time Gateway Interface
(function () {
  if (window.__TP_SNIPER_INJECTED__) return;
  window.__TP_SNIPER_INJECTED__ = true;

  function getEventId() {
    const match = window.location.pathname.match(/(?:kupit-bilet|events?)\/(\d+)/);
    return match ? match[1] : null;
  }

  function getEventName() {
    const h1 = document.querySelector("h1");
    if (h1 && h1.innerText.trim()) return h1.innerText.trim();
    const title = document.title;
    if (title) return title.split("|")[0].trim();
    return "Событие";
  }

  const currentEventId = getEventId();
  const currentEventName = currentEventId ? getEventName() : null;

  let selectedPrices = new Set();
  let activeTab = currentEventId ? "current" : "all";
  let eventSource = null;
  let currentActiveTaskId = null;

  function createWidget() {
    const container = document.createElement("div");
    container.id = "tp-sniper-widget";

    container.innerHTML = `
      <div class="tp-widget-header" id="tp-widget-toggle">
        <div class="tp-header-title">
          <span>⚡ Sniper Hub</span>
          <span class="tp-badge-total" id="tp-total-counter">Корзины: 0</span>
        </div>
        <div class="tp-header-actions">
          <button class="tp-btn-icon" id="tp-min-btn" title="Свернуть/Развернуть">_</button>
        </div>
      </div>

      <div class="tp-nav-tabs">
        ${currentEventId
        ? `<button class="tp-tab-btn tp-active-tab" id="tp-tab-current">⚡ Текущее</button>`
        : ""
      }
        <button class="tp-tab-btn ${!currentEventId ? "tp-active-tab" : ""}" id="tp-tab-all">
          🎯 Мои задачи (<span id="tp-tasks-count">0</span>)
        </button>
      </div>

      <div class="tp-widget-body" id="tp-body">
        <div id="tp-error-container"></div>
        
        <!-- TAB 1: Current Event -->
        ${currentEventId
        ? `
        <div id="tp-tab-pane-current">
          <div id="tp-controls-section">
            <div class="tp-status-row">
              <span style="font-weight:600; color:#f1f5f9; max-width:240px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${currentEventName}">${currentEventName}</span>
              <span id="tp-live-status" class="tp-status-live" style="display:none;">
                <span class="tp-status-dot"></span> В поиске
              </span>
            </div>

            <div class="tp-field-group">
              <div class="tp-label">
                <span>Количество мест:</span>
                <span id="tp-target-display">1 шт.</span>
              </div>
              <div class="tp-counter-control">
                <button class="tp-counter-btn" id="tp-count-dec">-</button>
                <input type="number" id="tp-count-input" class="tp-counter-input" value="1" min="1" max="10" />
                <button class="tp-counter-btn" id="tp-count-inc">+</button>
              </div>
            </div>

            <div class="tp-field-group">
              <div class="tp-label">
                <span>Фильтр по цене:</span>
                <span id="tp-price-count">Все цены</span>
              </div>
              <div class="tp-price-pills" id="tp-price-container">
                <div style="color:#94a3b8; font-size:11px;">Загрузка категорий цен...</div>
              </div>
            </div>

            <div class="tp-field-group">
              <button id="tp-start-btn" class="tp-btn-primary">
                🚀 Создать задачу в SaaS
              </button>
              <button id="tp-stop-btn" class="tp-btn-danger" style="display:none;">
                🛑 Отменить задачу
              </button>
            </div>

            <div class="tp-field-group">
              <div class="tp-label">Live Console:</div>
              <div class="tp-console-box" id="tp-console">
                <div class="tp-log-item">> SaaS-клиент подключен</div>
              </div>
            </div>
          </div>
        </div>
        `
        : ""
      }

        <!-- TAB 2: All Tasks & Bookings -->
        <div id="tp-tab-pane-all" style="${currentEventId ? "display:none;" : ""}">
          <div style="font-size:11px; font-weight:600; color:#94a3b8; margin-bottom:6px; text-transform:uppercase;">📦 Готовые корзины</div>
          <div id="tp-bookings-container" style="display:flex; flex-direction:column; gap:6px; margin-bottom:12px;">
            <div style="color:#64748b; font-size:12px; text-align:center; padding:8px;">Нет активных броней</div>
          </div>

          <div style="font-size:11px; font-weight:600; color:#94a3b8; margin-bottom:6px; text-transform:uppercase;">🎯 Активные задачи</div>
          <div id="tp-tasks-container" style="display:flex; flex-direction:column; gap:8px;">
            <div style="color:#64748b; font-size:12px; text-align:center; padding:8px;">Нет активных задач</div>
          </div>
        </div>
      </div>
    `;

    document.body.appendChild(container);
    setupEvents(container);

    if (currentEventId) {
      loadPrices();
    }
    refreshTasksAndBookings();
    setInterval(refreshTasksAndBookings, 4000);
    connectGatewaySSE();
  }

  function logConsole(msg, color = "#38bdf8") {
    const box = document.getElementById("tp-console");
    if (!box) return;
    const time = new Date().toLocaleTimeString();
    const item = document.createElement("div");
    item.className = "tp-log-item";
    item.style.color = color;
    item.textContent = `[${time}] ${msg}`;
    box.appendChild(item);
    box.scrollTop = box.scrollHeight;
  }

  function showErrorBanner(title, message) {
    const container = document.getElementById("tp-error-container");
    if (!container) return;
    container.innerHTML = `
      <div class="tp-error-banner" id="tp-active-error">
        <div class="tp-error-header">
          <div class="tp-error-title">⚠️ ${title}</div>
          <button class="tp-error-close" onclick="this.closest('.tp-error-banner').remove()">&times;</button>
        </div>
        <div class="tp-error-msg">${message}</div>
      </div>
    `;
  }

  async function loadPrices() {
    const container = document.getElementById("tp-price-container");
    if (!container) return;

    let pricesList = null;

    // 1. Попытка извлечь из DOM
    try {
      if (typeof prices_of_event !== "undefined" && prices_of_event) {
        const raw = typeof prices_of_event === "string" ? JSON.parse(prices_of_event) : prices_of_event;
        pricesList = Object.values(raw);
      }
    } catch (e) {
      console.debug("Prices not in DOM:", e);
    }

    // 2. Fallback через Ticketpro API
    if (!pricesList || pricesList.length === 0) {
      try {
        const res = await fetch(`/ticket-api/v1/get-scheme-prices-grouped/${currentEventId}`);
        if (res.ok) {
          const data = await res.json();
          if (data && data.prices) {
            pricesList = data.prices;
          }
        }
      } catch (e) {
        console.debug("Could not fetch scheme prices:", e);
      }
    }

    if (!pricesList || pricesList.length === 0) {
      container.innerHTML = `<div style="color:#94a3b8; font-size:11px;">Цены выбираются автоматически</div>`;
      return;
    }

    container.innerHTML = "";
    pricesList.forEach((p) => {
      const pill = document.createElement("div");
      pill.className = "tp-pill";
      pill.dataset.priceId = p.id;
      pill.innerHTML = `
        <span class="tp-pill-dot" style="background: ${p.color || "#3b82f6"}"></span>
        <span>${p.price || p.name || p.id} BYN</span>
      `;
      pill.addEventListener("click", () => {
        if (selectedPrices.has(p.id)) {
          selectedPrices.delete(p.id);
          pill.classList.remove("tp-pill-active");
        } else {
          selectedPrices.add(p.id);
          pill.classList.add("tp-pill-active");
        }
        const cnt = document.getElementById("tp-price-count");
        if (cnt) {
          cnt.textContent = selectedPrices.size > 0 ? `${selectedPrices.size} выбрано` : "Все цены";
        }
      });
      container.appendChild(pill);
    });
  }

  function setupEvents(container) {
    const minBtn = container.querySelector("#tp-min-btn");
    const body = container.querySelector("#tp-body");
    minBtn.addEventListener("click", () => {
      body.classList.toggle("tp-body-collapsed");
      minBtn.textContent = body.classList.contains("tp-body-collapsed") ? "+" : "_";
    });

    const tabCurrent = container.querySelector("#tp-tab-current");
    const tabAll = container.querySelector("#tp-tab-all");
    const paneCurrent = container.querySelector("#tp-tab-pane-current");
    const paneAll = container.querySelector("#tp-tab-pane-all");

    if (tabCurrent) {
      tabCurrent.addEventListener("click", () => {
        tabCurrent.classList.add("tp-active-tab");
        if (tabAll) tabAll.classList.remove("tp-active-tab");
        if (paneCurrent) paneCurrent.style.display = "block";
        if (paneAll) paneAll.style.display = "none";
      });
    }

    if (tabAll) {
      tabAll.addEventListener("click", () => {
        tabAll.classList.add("tp-active-tab");
        if (tabCurrent) tabCurrent.classList.remove("tp-active-tab");
        if (paneAll) paneAll.style.display = "block";
        if (paneCurrent) paneCurrent.style.display = "none";
        refreshTasksAndBookings();
      });
    }

    const countInput = container.querySelector("#tp-count-input");
    const countDisplay = container.querySelector("#tp-target-display");
    const decBtn = container.querySelector("#tp-count-dec");
    const incBtn = container.querySelector("#tp-count-inc");

    if (countInput && countDisplay) {
      const updateCount = (val) => {
        val = Math.max(1, Math.min(10, val));
        countInput.value = val;
        countDisplay.textContent = `${val} шт.`;
      };
      if (decBtn) decBtn.addEventListener("click", () => updateCount(parseInt(countInput.value || 1) - 1));
      if (incBtn) incBtn.addEventListener("click", () => updateCount(parseInt(countInput.value || 1) + 1));
      countInput.addEventListener("change", () => updateCount(parseInt(countInput.value || 1)));
    }

    const startBtn = container.querySelector("#tp-start-btn");
    const stopBtn = container.querySelector("#tp-stop-btn");

    if (startBtn && stopBtn) {
      startBtn.addEventListener("click", async () => {
        startBtn.disabled = true;
        logConsole("Отправка задачи в SaaS Backend...", "#38bdf8");

        try {
          const targetTickets = parseInt(countInput ? countInput.value : 1) || 1;
          const resp = await fetch("/api/gateway/tasks", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              event_id: currentEventId,
              target_tickets: targetTickets,
              filter_boxes: [],
            }),
          });

          const data = await resp.json();
          if (resp.ok) {
            currentActiveTaskId = data.task_id;
            logConsole(`✓ Задача принята SaaS сервером (ID: ${data.task_id.slice(0, 8)})`, "#10b981");
            setRunningUI(targetTickets, 0);
            refreshTasksAndBookings();
          } else {
            showErrorBanner("Ошибка создания задачи", data.detail || "Не удалось создать задачу");
            logConsole(`❌ ${data.detail || "Ошибка"}`, "#ef4444");
            resetUI();
          }
        } catch (err) {
          showErrorBanner("Сетевой сбой", err.message);
          logConsole(`Сбой сети: ${err.message}`, "#ef4444");
          resetUI();
        } finally {
          startBtn.disabled = false;
        }
      });

      stopBtn.addEventListener("click", async () => {
        if (!currentActiveTaskId) return;
        try {
          await fetch(`/api/gateway/tasks/${currentActiveTaskId}`, { method: "DELETE" });
          logConsole("Задача отменена в SaaS", "#f59e0b");
        } catch (e) {
          console.error("Error cancelling task:", e);
        } finally {
          currentActiveTaskId = null;
          resetUI();
          refreshTasksAndBookings();
        }
      });
    }
  }

  function setRunningUI(target, booked) {
    const startBtn = document.getElementById("tp-start-btn");
    const stopBtn = document.getElementById("tp-stop-btn");
    const liveStatus = document.getElementById("tp-live-status");
    if (startBtn) startBtn.style.display = "none";
    if (stopBtn) stopBtn.style.display = "block";
    if (liveStatus) {
      liveStatus.style.display = "inline-flex";
      liveStatus.innerHTML = `<span class="tp-status-dot"></span> Поиск (${booked}/${target})`;
    }
  }

  function resetUI() {
    const startBtn = document.getElementById("tp-start-btn");
    const stopBtn = document.getElementById("tp-stop-btn");
    const liveStatus = document.getElementById("tp-live-status");
    if (startBtn) {
      startBtn.disabled = false;
      startBtn.style.display = "block";
    }
    if (stopBtn) stopBtn.style.display = "none";
    if (liveStatus) liveStatus.style.display = "none";
  }

  async function refreshTasksAndBookings() {
    try {
      // 1. Получение задач пользователя
      const resTasks = await fetch("/api/gateway/tasks");
      if (resTasks.ok) {
        const tasks = await resTasks.json();
        const tasksCountEl = document.getElementById("tp-tasks-count");
        if (tasksCountEl) tasksCountEl.textContent = tasks.length;

        const container = document.getElementById("tp-tasks-container");
        if (container) {
          if (tasks.length === 0) {
            container.innerHTML = `<div style="color:#64748b; font-size:12px; text-align:center; padding:8px;">Нет активных задач</div>`;
          } else {
            container.innerHTML = "";
            tasks.forEach((t) => {
              const card = document.createElement("div");
              card.className = "tp-task-card";
              const isDone = t.booked_count >= t.target_tickets || t.status === "completed";

              if (t.event_id === currentEventId && !isDone && t.status === "active") {
                currentActiveTaskId = t.task_id;
                setRunningUI(t.target_tickets, t.booked_count);
              }

              card.innerHTML = `
                <div class="tp-task-header">
                  <span style="font-weight:600; color:#f8fafc;">Событие #${t.event_id}</span>
                  <span class="${isDone ? '' : 'tp-status-live'}" style="font-size:11px;">
                    ${isDone ? '✓ Завершена' : '<span class="tp-status-dot"></span> В поиске'}
                  </span>
                </div>
                <div class="tp-task-meta">
                  <span>Прогресс: <b>${t.booked_count}/${t.target_tickets}</b></span>
                  <span>Статус: ${t.status}</span>
                </div>
                ${!isDone && t.status === "active" ? `
                  <div class="tp-task-actions" style="margin-top:6px;">
                    <button class="tp-btn-danger tp-btn-sm" style="flex:1;" onclick="window.__tp_cancel_task('${t.task_id}')">🛑 Отменить</button>
                  </div>
                ` : ''}
              `;
              container.appendChild(card);
            });
          }
        }
      }

      // 2. Получение активных корзин
      const resBookings = await fetch("/api/gateway/bookings");
      if (resBookings.ok) {
        const bookings = await resBookings.json();
        const totalEl = document.getElementById("tp-total-counter");
        if (totalEl) totalEl.textContent = `Корзины: ${bookings.length}`;

        const bContainer = document.getElementById("tp-bookings-container");
        if (bContainer) {
          if (bookings.length === 0) {
            bContainer.innerHTML = `<div style="color:#64748b; font-size:12px; text-align:center; padding:8px;">Нет активных броней</div>`;
          } else {
            bContainer.innerHTML = "";
            bookings.forEach((b) => {
              const bCard = document.createElement("div");
              bCard.className = "tp-task-card";
              bCard.style.borderColor = "#10b981";
              bCard.innerHTML = `
                <div class="tp-task-header">
                  <span style="font-weight:600; color:#34d399;">🎟️ Билет #${b.ticket_id}</span>
                  <span class="tp-timer-badge">⏱ ${b.time_left_sec} сек</span>
                </div>
                <div class="tp-task-meta">
                  <span>${b.seat_info || "Сектор"}</span>
                  <b>${b.price} BYN</b>
                </div>
                <div class="tp-task-actions" style="margin-top:6px;">
                  <button class="tp-btn-primary tp-btn-sm" style="flex:1;" onclick="window.__tp_claim_booking('${b.booking_id}')">🛒 Забрать в корзину</button>
                </div>
              `;
              bContainer.appendChild(bCard);
            });
          }
        }
      }
    } catch (e) {
      console.debug("Could not refresh tasks/bookings:", e);
    }
  }

  window.__tp_cancel_task = async function (taskId) {
    try {
      await fetch(`/api/gateway/tasks/${taskId}`, { method: "DELETE" });
      refreshTasksAndBookings();
    } catch (e) {
      console.error("Error cancelling task:", e);
    }
  };

  window.__tp_claim_booking = async function (bookingId) {
    try {
      const resp = await fetch("/api/gateway/bookings/claim", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ booking_id: bookingId }),
      });
      const data = await resp.json();
      if (resp.ok) {
        // Установка сессионных кук в браузере
        if (data.cookies) {
          for (const [name, val] of Object.entries(data.cookies)) {
            document.cookie = `${name}=${val}; path=/; SameSite=Lax`;
          }
        }
        window.location.href = "/order/auth/";
      } else {
        alert(data.detail || "Бронь просрочена");
        refreshTasksAndBookings();
      }
    } catch (e) {
      console.error("Error claiming booking:", e);
      window.location.href = "/order/auth/";
    }
  };

  function connectGatewaySSE() {
    if (eventSource) return;
    try {
      eventSource = new EventSource("/api/gateway/stream");
      eventSource.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          if (data.type === "booking_allocated") {
            logConsole(`🎉 Пойман билет #${data.ticket_id}! (${data.price_value} BYN)`, "#10b981");
            refreshTasksAndBookings();
          }
        } catch (err) {
          console.debug("SSE parse error:", err);
        }
      };
      eventSource.onerror = () => {
        if (eventSource) {
          eventSource.close();
          eventSource = null;
        }
        setTimeout(connectGatewaySSE, 4000);
      };
    } catch (e) {
      console.warn("SSE connection error:", e);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", createWidget);
  } else {
    createWidget();
  }
})();
