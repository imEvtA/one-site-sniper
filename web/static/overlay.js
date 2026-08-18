// Ticketpro Fast Sniper - Multi-Event HUD Hub & Session Manager
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

  function createWidget() {
    const container = document.createElement("div");
    container.id = "tp-sniper-widget";

    container.innerHTML = `
      <div class="tp-widget-header" id="tp-widget-toggle">
        <div class="tp-header-title">
          <span>⚡ Sniper Hub</span>
          <span class="tp-badge-total" id="tp-total-counter">Всего: 0</span>
        </div>
        <div class="tp-header-actions">
          <button class="tp-btn-icon" id="tp-min-btn" title="Свернуть/Развернуть">_</button>
        </div>
      </div>

      <div class="tp-nav-tabs">
        ${
          currentEventId
            ? `<button class="tp-tab-btn tp-active-tab" id="tp-tab-current">⚡ Текущее</button>`
            : ""
        }
        <button class="tp-tab-btn ${
          !currentEventId ? "tp-active-tab" : ""
        }" id="tp-tab-all">🎯 Все снайперы (<span id="tp-tasks-count">0</span>)</button>
      </div>

      <div class="tp-widget-body" id="tp-body">
        <!-- TAB 1: Current Event -->
        ${
          currentEventId
            ? `
        <div id="tp-tab-pane-current">
          <div class="tp-status-row">
            <span style="font-weight:600; color:#f1f5f9; max-width:240px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${currentEventName}">${currentEventName}</span>
            <span id="tp-live-status" class="tp-status-live" style="display:none;">
              <span class="tp-status-dot"></span> Снайпер
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
              🚀 Запустить снайпер
            </button>
            <button id="tp-stop-btn" class="tp-btn-danger" style="display:none;">
              🛑 Остановить
            </button>
          </div>

          <div class="tp-field-group">
            <div class="tp-label">Live Console:</div>
            <div class="tp-console-box" id="tp-console">
              <div class="tp-log-item">> Снайпер готов к запуску</div>
            </div>
          </div>
        </div>
        `
            : ""
        }

        <!-- TAB 2: All Tasks -->
        <div id="tp-tab-pane-all" style="${currentEventId ? "display:none;" : ""}">
          <div id="tp-tasks-container" style="display:flex; flex-direction:column; gap:8px;">
            <div style="color:#94a3b8; font-size:12px; text-align:center; padding:12px;">Нет активных снайперов</div>
          </div>
        </div>
      </div>
    `;

    document.body.appendChild(container);
    setupEvents(container);
    if (currentEventId) {
      loadPrices();
      checkCurrentStatus();
    }
    refreshTasksList();
    setInterval(refreshTasksList, 3000);
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

  function loadPrices() {
    const container = document.getElementById("tp-price-container");
    if (!container) return;

    let pricesObj = null;
    try {
      if (typeof prices_of_event !== "undefined" && prices_of_event) {
        pricesObj = typeof prices_of_event === "string" ? JSON.parse(prices_of_event) : prices_of_event;
      }
    } catch (e) {
      console.warn("Error parsing prices_of_event:", e);
    }

    if (!pricesObj || Object.keys(pricesObj).length === 0) {
      container.innerHTML = `<div style="color:#94a3b8; font-size:11px;">Цены выбираются автоматически</div>`;
      return;
    }

    container.innerHTML = "";
    Object.values(pricesObj).forEach((p) => {
      const pill = document.createElement("div");
      pill.className = "tp-pill";
      pill.dataset.priceId = p.id;
      pill.innerHTML = `
        <span class="tp-pill-dot" style="background: ${p.color || "#3b82f6"}"></span>
        <span>${p.price} BYN</span>
      `;
      pill.addEventListener("click", () => {
        if (selectedPrices.has(p.id)) {
          selectedPrices.delete(p.id);
          pill.classList.remove("tp-active");
        } else {
          selectedPrices.add(p.id);
          pill.classList.add("tp-active");
        }
        document.getElementById("tp-price-count").textContent =
          selectedPrices.size > 0 ? `Выбрано: ${selectedPrices.size}` : "Все цены";
      });
      container.appendChild(pill);
    });
  }

  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta) return meta.getAttribute("content");
    const input = document.querySelector('input[name="_csrf-frontend"], input[name="_csrf"]');
    if (input) return input.value;
    return null;
  }

  function setupEvents(container) {
    const minBtn = document.getElementById("tp-min-btn");
    const header = document.getElementById("tp-widget-toggle");
    const tabCurrent = document.getElementById("tp-tab-current");
    const tabAll = document.getElementById("tp-tab-all");
    const paneCurrent = document.getElementById("tp-tab-pane-current");
    const paneAll = document.getElementById("tp-tab-pane-all");

    header.addEventListener("click", (e) => {
      if (e.target.tagName !== "BUTTON") {
        container.classList.toggle("tp-minimized");
      }
    });

    minBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      container.classList.toggle("tp-minimized");
    });

    if (tabCurrent && paneCurrent) {
      tabCurrent.addEventListener("click", () => {
        tabCurrent.classList.add("tp-active-tab");
        tabAll.classList.remove("tp-active-tab");
        paneCurrent.style.display = "block";
        paneAll.style.display = "none";
      });
    }

    if (tabAll && paneAll) {
      tabAll.addEventListener("click", () => {
        if (tabCurrent) tabCurrent.classList.remove("tp-active-tab");
        tabAll.classList.add("tp-active-tab");
        if (paneCurrent) paneCurrent.style.display = "none";
        paneAll.style.display = "block";
        refreshTasksList();
      });
    }

    if (currentEventId) {
      const countInput = document.getElementById("tp-count-input");
      const countDisplay = document.getElementById("tp-target-display");
      const decBtn = document.getElementById("tp-count-dec");
      const incBtn = document.getElementById("tp-count-inc");
      const startBtn = document.getElementById("tp-start-btn");
      const stopBtn = document.getElementById("tp-stop-btn");

      function updateCount(val) {
        const v = Math.max(1, Math.min(10, parseInt(val) || 1));
        countInput.value = v;
        countDisplay.textContent = `${v} шт.`;
      }

      decBtn.addEventListener("click", () => updateCount(parseInt(countInput.value) - 1));
      incBtn.addEventListener("click", () => updateCount(parseInt(countInput.value) + 1));
      countInput.addEventListener("input", (e) => updateCount(e.target.value));

      startBtn.addEventListener("click", async () => {
        const targetTickets = parseInt(countInput.value) || 1;
        const allowedPrices = Array.from(selectedPrices);
        const csrfToken = getCsrfToken();

        setRunningUI(targetTickets, 0);
        logConsole(`Запуск снайпера на ${targetTickets} билетов...`, "#a855f7");

        try {
          connectSSE(currentEventId);

          const resp = await fetch("/api/bot/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              event_id: currentEventId,
              event_name: currentEventName,
              target_tickets: targetTickets,
              allowed_price_ids: allowedPrices.length > 0 ? allowedPrices : null,
              csrf_token: csrfToken,
              num_consumers: 5
            })
          });

          const data = await resp.json();
          if (data.status === "ok") {
            logConsole("Снайпер запущен и охотится за билетами", "#10b981");
            refreshTasksList();
          } else {
            logConsole(`Ошибка запуска: ${data.message}`, "#ef4444");
            resetUI();
          }
        } catch (err) {
          logConsole(`Сетевая ошибка: ${err.message}`, "#ef4444");
          resetUI();
        }
      });

      stopBtn.addEventListener("click", async () => {
        try {
          await fetch("/api/bot/stop", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ event_id: currentEventId })
          });
          logConsole("Снайпер остановлен", "#f59e0b");
        } catch (e) {
          logConsole("Ошибка при остановке", "#ef4444");
        } finally {
          resetUI();
          refreshTasksList();
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
      liveStatus.innerHTML = `<span class="tp-status-dot"></span> Снайпер (${booked}/${target})`;
    }
  }

  function resetUI() {
    const startBtn = document.getElementById("tp-start-btn");
    const stopBtn = document.getElementById("tp-stop-btn");
    const liveStatus = document.getElementById("tp-live-status");
    if (startBtn) startBtn.style.display = "block";
    if (stopBtn) stopBtn.style.display = "none";
    if (liveStatus) liveStatus.style.display = "none";
  }

  async function checkCurrentStatus() {
    if (!currentEventId) return;
    try {
      const resp = await fetch(`/api/bot/status?event_id=${currentEventId}`);
      const data = await resp.json();
      if (data.status === "running") {
        const countInput = document.getElementById("tp-count-input");
        const countDisplay = document.getElementById("tp-target-display");
        if (countInput) countInput.value = data.target;
        if (countDisplay) countDisplay.textContent = `${data.target} шт.`;

        setRunningUI(data.target, data.booked);
        logConsole(`> Восстановлено подключение (${data.booked}/${data.target})`, "#10b981");
        connectSSE(currentEventId);
      }
    } catch (err) {
      console.warn("Could not check current status:", err);
    }
  }

  async function refreshTasksList() {
    try {
      const resp = await fetch("/api/bot/tasks");
      const data = await resp.json();

      const totalCounter = document.getElementById("tp-total-counter");
      if (totalCounter) {
        totalCounter.textContent = `Всего: ${data.total_booked || 0}`;
      }

      const tasksCount = document.getElementById("tp-tasks-count");
      if (tasksCount) {
        tasksCount.textContent = (data.tasks || []).length;
      }

      const container = document.getElementById("tp-tasks-container");
      if (!container) return;

      if (!data.tasks || data.tasks.length === 0) {
        container.innerHTML = `<div style="color:#94a3b8; font-size:12px; text-align:center; padding:12px;">Нет активных снайперов</div>`;
        return;
      }

      container.innerHTML = "";
      data.tasks.forEach((t) => {
        const card = document.createElement("div");
        card.className = "tp-task-card";

        const isRunning = t.status === "running";
        const statusBadge = isRunning
          ? `<span class="tp-status-live" style="font-size:11px;"><span class="tp-status-dot"></span> Поиск</span>`
          : `<span style="color:#94a3b8; font-size:11px;">Завершен</span>`;

        card.innerHTML = `
          <div class="tp-task-header">
            <a href="/kupit-bilet/${t.event_id}/" class="tp-task-title" style="text-decoration:none;" title="${t.event_name}">
              ${t.event_name}
            </a>
            ${statusBadge}
          </div>
          <div class="tp-task-meta">
            <span>Прогресс: <b>${t.booked}/${t.target}</b></span>
            ${t.time_live ? `<span class="tp-timer-badge">⏱ ${t.time_live}</span>` : ""}
          </div>
          <div class="tp-task-actions">
            ${
              isRunning
                ? `<button class="tp-btn-danger tp-btn-sm" style="flex:1;" onclick="window.__tp_stop_task('${t.event_id}')">🛑 Стоп</button>`
                : ""
            }
            ${
              t.booked > 0
                ? `<button class="tp-btn-primary tp-btn-sm" style="flex:1;" onclick="window.__tp_go_to_basket('${t.event_id}')">🛒 В корзину (${t.booked})</button>`
                : `<a href="/kupit-bilet/${t.event_id}/" class="tp-btn-primary tp-btn-sm" style="flex:1; text-align:center;">Открыть</a>`
            }
          </div>
        `;
        container.appendChild(card);
      });
    } catch (e) {
      console.warn("Could not refresh tasks list:", e);
    }
  }

  window.__tp_stop_task = async function (eid) {
    try {
      await fetch("/api/bot/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ event_id: eid })
      });
      refreshTasksList();
      if (eid === currentEventId) resetUI();
    } catch (e) {
      console.error("Error stopping task:", e);
    }
  };

  window.__tp_go_to_basket = async function (eid) {
    try {
      await fetch("/api/bot/activate-session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ event_id: eid })
      });
      window.location.href = "/order/auth/";
    } catch (e) {
      console.error("Error activating session:", e);
      window.location.href = "/order/auth/";
    }
  };

  function triggerBasketRefresh() {
    try {
      if (window.$) {
        window.$.post("/api/ticket/get-basket/", function (res) {
          console.log("Basket refreshed:", res);
        });
      } else {
        fetch("/api/ticket/get-basket/", { method: "POST" });
      }
    } catch (e) {
      console.warn("Could not auto-refresh basket:", e);
    }
  }

  function connectSSE(eid) {
    if (eventSource) eventSource.close();
    eventSource = new EventSource(`/api/bot/stream?event_id=${eid}`);

    eventSource.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.type === "status") {
          logConsole(`> ${data.message}`, "#38bdf8");
        } else if (data.type === "session_initialized") {
          logConsole("✓ Сессия получена, схема найдена", "#10b981");
        } else if (data.type === "tickets_streamed") {
          logConsole(`✓ Найдено свободных мест: ${data.found_count}`, "#60a5fa");
        } else if (data.type === "ticket_booked") {
          logConsole(`🎯 Забронировано место ${data.ticket_id}! (${data.booked}/${data.target})`, "#10b981");
          triggerBasketRefresh();
          refreshTasksList();
          const liveStatus = document.getElementById("tp-live-status");
          if (liveStatus) {
            liveStatus.innerHTML = `<span class="tp-status-dot"></span> Снайпер (${data.booked}/${data.target})`;
          }
        } else if (data.type === "finished") {
          logConsole(`🎉 Снайпер завершил работу! Забронировано: ${data.booked}/${data.target}`, "#10b981");
          triggerBasketRefresh();
          refreshTasksList();
          resetUI();
        } else if (data.type === "error") {
          logConsole(`❌ ${data.message}`, "#ef4444");
          resetUI();
          refreshTasksList();
        }
      } catch (err) {
        console.error("SSE parse error:", err);
      }
    };
  }

  // Initialize widget when DOM is ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", createWidget);
  } else {
    createWidget();
  }
})();
