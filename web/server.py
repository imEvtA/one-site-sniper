import asyncio
import json
import logging
import os
import time
from typing import Any
from pathlib import Path


from fastapi import FastAPI, Request, Response, Query
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import httpx

from core.runner import Core
from core.tasks.parser import DefaultParser

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("web.server")

TARGET_HOST = "https://www.ticketpro.by"
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Ticketpro Reverse Proxy & Fast Sniper")

# Mount overlay static assets
app.mount("/proxy-static", StaticFiles(directory=STATIC_DIR), name="proxy-static")

# Registry for active bot background tasks and event broadcast queues
active_sessions: dict[str, dict[str, Any]] = {}


class StartBotRequest(BaseModel):
    event_id: str
    event_name: str | None = None
    target_tickets: int = 1
    num_consumers: int = 5
    allowed_price_ids: list[str] | None = None
    allowed_sectors: list[str] | None = None
    csrf_token: str | None = None


class StopBotRequest(BaseModel):
    event_id: str


class ActivateSessionRequest(BaseModel):
    event_id: str


# ==========================================
# BOT CONTROL API
# ==========================================

@app.post("/api/bot/start")
async def start_bot(request: Request, req: StartBotRequest):
    event_id = req.event_id
    event_name = req.event_name or f"Событие #{event_id}"

    # Extract user's browser session cookies
    browser_cookies = dict(request.cookies)
    csrf_token = req.csrf_token or request.headers.get("x-csrf-token")
    logger.info(f"[BOT START] Event: {event_id} ({event_name}) | Cookies: {browser_cookies}")

    # Stop existing task if running for this event
    if event_id in active_sessions and active_sessions[event_id].get("task"):
        task = active_sessions[event_id]["task"]
        if not task.done():
            task.cancel()

    subscribers: set[asyncio.Queue[str]] = active_sessions.get(event_id, {}).get("subscribers", set())
    active_sessions[event_id] = {
        "event_id": event_id,
        "event_name": event_name,
        "subscribers": subscribers,
        "status": "running",
        "target": req.target_tickets,
        "booked": 0,
        "time_live": None,
        "cookies": browser_cookies.copy(),
    }

    async def emit_callback(event_data: dict[str, Any]):
        if event_data.get("type") == "ticket_booked":
            active_sessions[event_id]["booked"] = event_data.get("booked", active_sessions[event_id]["booked"])
        msg = json.dumps({"event_id": event_id, "event_name": event_name, **event_data})
        for q in list(subscribers):
            try:
                q.put_nowait(msg)
            except Exception:
                pass

    parser = DefaultParser(
        allowed_price_ids=req.allowed_price_ids,
        allowed_sectors=req.allowed_sectors
    )

    core = Core(
        event_id=event_id,
        target_tickets=req.target_tickets,
        num_consumers=req.num_consumers,
        parser=parser,
        event_callback=emit_callback
    )

    initial_headers = None
    if csrf_token:
        initial_headers = core.fetcher.headers_template.copy()
        initial_headers["X-CSRF-Token"] = csrf_token

    async def runner_wrapper():
        try:
            booked = await core.run(
                initial_cookies=browser_cookies if browser_cookies else None,
                initial_headers=initial_headers
            )
            active_sessions[event_id]["booked"] = booked
            active_sessions[event_id]["status"] = "finished"
        except asyncio.CancelledError:
            active_sessions[event_id]["status"] = "stopped"
            await emit_callback({"type": "status", "message": "Bot task stopped by user"})
        except Exception as e:
            logger.exception(f"Error in core runner for event {event_id}: {e}")
            active_sessions[event_id]["status"] = "error"
            await emit_callback({"type": "error", "message": str(e)})

    task = asyncio.create_task(runner_wrapper())
    active_sessions[event_id]["task"] = task

    return {"status": "ok", "message": f"Sniper started for {event_name}", "event_id": event_id}


@app.post("/api/bot/stop")
async def stop_bot(req: StopBotRequest):
    event_id = req.event_id
    if event_id in active_sessions:
        task = active_sessions[event_id].get("task")
        if task and not task.done():
            task.cancel()
        active_sessions[event_id]["status"] = "stopped"
        return {"status": "ok", "message": f"Sniper stopped for event {event_id}"}
    return {"status": "not_found", "message": f"No active session for event {event_id}"}


@app.get("/api/bot/tasks")
async def get_all_tasks():
    """
    Возвращает список всех активных и завершенных снайперов и общий счетчик
    """
    tasks_list = []
    total_booked = 0

    for eid, info in active_sessions.items():
        task = info.get("task")
        is_running = task is not None and not task.done()
        status = "running" if is_running else info.get("status", "idle")
        booked = info.get("booked", 0)
        total_booked += booked

        tasks_list.append({
            "event_id": eid,
            "event_name": info.get("event_name", f"Событие #{eid}"),
            "status": status,
            "target": info.get("target", 0),
            "booked": booked,
            "time_live": info.get("time_live"),
        })

    return {
        "total_booked": total_booked,
        "tasks": tasks_list
    }


@app.get("/api/bot/status")
async def get_bot_status(event_id: str = Query(...)):
    if event_id in active_sessions:
        info = active_sessions[event_id]
        task = info.get("task")
        is_running = task is not None and not task.done()
        current_status = "running" if is_running else info.get("status", "idle")
        return {
            "status": current_status,
            "event_name": info.get("event_name", f"Событие #{event_id}"),
            "target": info.get("target", 0),
            "booked": info.get("booked", 0),
            "time_live": info.get("time_live"),
        }
    return {"status": "idle", "target": 0, "booked": 0}


@app.post("/api/bot/activate-session")
async def activate_session(req: ActivateSessionRequest):
    """
    Подставляет сессионные куки выбранного снайпера в браузер
    """
    event_id = req.event_id
    if event_id in active_sessions:
        cookies = active_sessions[event_id].get("cookies", {})
        response = JSONResponse({"status": "ok", "message": f"Session switched to event {event_id}"})
        for k, v in cookies.items():
            response.set_cookie(key=k, value=v, path="/", httponly=True, samesite="lax")
        return response
    return JSONResponse({"status": "error", "message": "Session not found"}, status_code=404)


@app.get("/basket")
@app.get("/basket/")
@app.get("/cart")
@app.get("/cart/")
async def basket_redirect():
    """
    Ticketpro не имеет страницы /basket/ (выдает 404).
    Реальный эндпоинт оформления заказа — /order/auth/ или /order/basket/.
    """
    return Response(status_code=302, headers={"Location": "/order/auth/"})



@app.get("/api/bot/stream")
async def bot_stream(event_id: str = Query(...)):
    """
    Server-Sent Events (SSE) stream supporting multiple tabs and reloads
    """
    async def event_generator():
        q: asyncio.Queue[str] = asyncio.Queue()
        session_info = active_sessions.setdefault(event_id, {
            "subscribers": set(),
            "status": "idle",
            "target": 0,
            "booked": 0,
            "event_name": f"Событие #{event_id}"
        })
        subscribers_set = session_info.setdefault("subscribers", set())
        subscribers_set.add(q)

        task = session_info.get("task")
        is_running = task is not None and not task.done()
        current_status = "running" if is_running else session_info.get("status", "idle")

        initial_msg = {
            "type": "status",
            "event_id": event_id,
            "event_name": session_info.get("event_name"),
            "message": f"Connected to session (status: {current_status}, booked: {session_info.get('booked')}/{session_info.get('target')})"
        }
        yield f"data: {json.dumps(initial_msg)}\n\n"

        try:
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=20.0)
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                except asyncio.CancelledError:
                    break
        finally:
            subscribers_set.discard(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")




# ==========================================
# REVERSE PROXY ROUTER
# ==========================================

import re

EXCLUDE_REQUEST_HEADERS = {"host", "content-length"}
EXCLUDE_RESPONSE_HEADERS = {
    "content-length",
    "content-encoding",
    "content-security-policy",
    "content-security-policy-report-only",
    "transfer-encoding",
    "connection",
    "set-cookie"
}


def sanitize_set_cookie(header_value: str, is_secure: bool = False) -> str:
    """
    Удаляет domain=.ticketpro.by и заменяет SameSite=None на SameSite=Lax (без secure),
    чтобы современные браузеры (Chrome/Firefox) не отбрасывали куку на http://localhost:8000.
    """
    val = re.sub(r'(?i)\bdomain=[^;]+;?\s*', '', header_value)
    if not is_secure:
        val = re.sub(r'(?i)\bsecure;?\s*', '', val)
        val = re.sub(r'(?i)\bSameSite=None\b', 'SameSite=Lax', val)
    return val.strip().rstrip(';')



def extract_event_id(path: str, referer: str = "") -> str | None:
    match = re.search(r'(?:kupit-bilet|events?)/(\d+)', path)
    if match:
        return match.group(1)
    if referer:
        match_ref = re.search(r'(?:kupit-bilet|events?)/(\d+)', referer)
        if match_ref:
            return match_ref.group(1)
    return None


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"])
async def proxy_pass(request: Request, path: str):
    target_url = f"{TARGET_HOST}/{path}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"

    req_cookies = dict(request.cookies)
    referer_hdr = request.headers.get("referer", "")

    # Event-Aware Session Mapping: if this request belongs to a known active event session, use its cookies
    target_eid = extract_event_id(path, referer_hdr)
    if target_eid and target_eid in active_sessions and active_sessions[target_eid].get("cookies"):
        session_cookies = active_sessions[target_eid]["cookies"]
        req_cookies.update(session_cookies)
        logger.info(f"[EVENT-AWARE PROXY] Using sniper session cookies for event {target_eid}")

    logger.info(f"[PROXY REQ] {request.method} {request.url.path} | Browser Cookies: {req_cookies}")

    headers = dict(request.headers)
    headers = {k: v for k, v in headers.items() if k.lower() not in EXCLUDE_REQUEST_HEADERS}
    headers["Host"] = "www.ticketpro.by"
    headers["Referer"] = referer_hdr.replace(str(request.base_url), f"{TARGET_HOST}/") if referer_hdr else TARGET_HOST

    body = await request.body()


    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            resp = await client.request(
                method=request.method,
                url=target_url,
                headers=headers,
                cookies=req_cookies,
                content=body if body else None,
                timeout=25.0
            )
        except Exception as e:
            logger.error(f"[PROXY ERROR] {target_url}: {e}")
            return Response(content=f"Proxy Error: {e}", status_code=502)

    resp_headers = {}
    for k, v in resp.headers.items():
        if k.lower() not in EXCLUDE_RESPONSE_HEADERS:
            resp_headers[k] = v

    is_https = request.url.scheme == "https"
    raw_set_cookies = resp.headers.get_list("set-cookie")

    logger.info(f"[PROXY RESP] {resp.status_code} {request.url.path} | Upstream Set-Cookies: {raw_set_cookies}")

    # Special logging for basket API
    if "get-basket" in path:
        try:
            basket_json = resp.json()
            basket_info = basket_json.get("basket", {})
            items_count = basket_info.get("countTickets", 0)
            time_live = basket_info.get("time_live")
            logger.info(f"[BASKET CHECK] Tickets in basket: {items_count} | Time remaining: {time_live} | Items: {basket_info.get('items', [])}")
        except Exception as e:
            logger.warning(f"[BASKET CHECK] Could not parse basket json: {e}")

    content_type = resp.headers.get("content-type", "")

    # Inject overlay widget into HTML responses
    if "text/html" in content_type:
        html_text = resp.text
        v_ts = int(time.time())

        injection = f"""
        <!-- Injected Ticketpro Sniper HUD -->
        <link rel="stylesheet" href="/proxy-static/overlay.css?v={v_ts}">
        <script src="/proxy-static/overlay.js?v={v_ts}"></script>
        """

        if "</body>" in html_text:
            html_text = html_text.replace("</body>", f"{injection}\n</body>")
        else:
            html_text += injection

        response = HTMLResponse(
            content=html_text,
            status_code=resp.status_code,
            headers=resp_headers
        )
    else:
        response = Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=resp_headers,
            media_type=content_type
        )

    # Append sanitized set-cookie headers
    for cookie_header in raw_set_cookies:
        sanitized = sanitize_set_cookie(cookie_header, is_secure=is_https)
        if sanitized:
            logger.info(f"[SET-COOKIE SANITIZED] {sanitized}")
            response.headers.append("set-cookie", sanitized)

    return response




def main():
    import uvicorn
    uvicorn.run("web.server:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
