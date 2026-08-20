import logging
import re
import time

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse

from core.bot import bot_manager
from core.tasks.utils.constants import TARGET_HOST, TARGET_HOST_HEADER

logger = logging.getLogger("web.proxy")

router = APIRouter()

EXCLUDE_REQUEST_HEADERS = {"host", "content-length"}
EXCLUDE_RESPONSE_HEADERS = {
    "content-length",
    "content-encoding",
    "content-security-policy",
    "content-security-policy-report-only",
    "transfer-encoding",
    "connection",
    "set-cookie",
    "location",
}


def sanitize_set_cookie(header_value: str, is_secure: bool = False) -> str:
    """
    Удаляет domain=.ticketpro.by и заменяет SameSite=None на SameSite=Lax,
    чтобы браузер не отбрасывал куку на http://localhost:8000.
    """
    val = re.sub(r"(?i)\bdomain=[^;]+;?\s*", "", header_value)
    if not is_secure:
        val = re.sub(r"(?i)\bsecure;?\s*", "", val)
        val = re.sub(r"(?i)\bSameSite=None\b", "SameSite=Lax", val)
    return val.strip().rstrip(";")


def extract_event_id(path: str, referer: str = "") -> str | None:
    """
    Определяет event_id из URL страницы или Referer (для AJAX запросов схемы/билетов).
    """
    if path.startswith(("order/", "basket", "auth/", "korzina")):
        return None

    match = re.search(r"(?:kupit-bilet|events?)/(\d+)", path)
    if match:
        return match.group(1)
    if referer and (path.startswith("api/ticket/") or path.startswith("ticket-api/")):
        match_ref = re.search(r"(?:kupit-bilet|events?)/(\d+)", referer)
        if match_ref:
            return match_ref.group(1)
    return None


def _rewrite_redirect_location(location_hdr: str) -> str:
    """
    Переписывает Location в 3xx редиректах, удерживая браузер на локальном прокси.
    """
    rewritten = location_hdr.replace(TARGET_HOST, "").replace(TARGET_HOST.replace("https://", "http://"), "")
    return rewritten if rewritten else "/"


def _inject_hud(html_text: str, status_code: int) -> str:
    """
    Инжектирует HUD оверлей снайпера и статус страницы в HTML.
    """
    v_ts = int(time.time())
    injection = f"""
    <!-- Injected Ticketpro Sniper HUD -->
    <script>window.__TP_PAGE_STATUS__ = {status_code};</script>
    <link rel="stylesheet" href="/proxy-static/overlay.css?v={v_ts}">
    <script src="/proxy-static/overlay.js?v={v_ts}"></script>
    """
    if "</body>" in html_text:
        return html_text.replace("</body>", f"{injection}\n</body>")
    return html_text + injection


@router.get("/korzina")
@router.get("/korzina/")
@router.get("/basket")
@router.get("/basket/")
@router.get("/cart")
@router.get("/cart/")
async def direct_checkout_redirect():
    """
    Напрямую отправляет пользователя на экран оформления заказа (/order/auth/).
    """
    return Response(status_code=302, headers={"Location": "/order/auth/"})


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"])
async def proxy_pass(request: Request, path: str):
    """
    Основной реверс-прокси роут к целевой платформе.
    """
    # Нормализация URL мероприятия (добавление обязательного слэша на конце)
    if re.match(r"^(?:kupit-bilet|events?)/\d+$", path):
        target_path = f"/{path}/"
        if request.url.query:
            target_path = f"{target_path}?{request.url.query}"
        return Response(status_code=301, headers={"Location": target_path})

    target_url = f"{TARGET_HOST}/{path}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"

    req_cookies = dict(request.cookies)
    referer_hdr = request.headers.get("referer", "")
    target_eid = extract_event_id(path, referer_hdr)

    # Обогащение куками сессии или предсессии снайпера для текущего мероприятия
    if target_eid:
        session = bot_manager.get(target_eid)
        if session and session.ctx.cookies:
            req_cookies.update(session.ctx.cookies)
        else:
            presession = bot_manager.get_presession(target_eid)
            if presession and presession.cookies:
                req_cookies.update(presession.cookies)

    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in EXCLUDE_REQUEST_HEADERS
    }
    headers["Host"] = TARGET_HOST_HEADER
    headers["Referer"] = referer_hdr.replace(str(request.base_url), f"{TARGET_HOST}/") if referer_hdr else TARGET_HOST

    body = await request.body()

    async with httpx.AsyncClient(follow_redirects=False) as client:
        try:
            resp = await client.request(
                method=request.method,
                url=target_url,
                headers=headers,
                cookies=req_cookies,
                content=body if body else None,
                timeout=25.0,
            )
        except Exception as e:
            logger.error(f"[PROXY ERROR] {target_url}: {e}")
            return Response(content=f"Proxy Error: {e}", status_code=502)

    resp_headers = {
        k: v for k, v in resp.headers.items()
        if k.lower() not in EXCLUDE_RESPONSE_HEADERS
    }

    # 1. Редиректы (3xx)
    if resp.status_code in (301, 302, 303, 307, 308) or "location" in resp.headers:
        loc = resp.headers.get("location", "")
        if loc:
            resp_headers["Location"] = _rewrite_redirect_location(loc)
        response = Response(content=resp.content, status_code=resp.status_code, headers=resp_headers)

    # 2. HTML страницы (инъекция HUD + сохранение PresessionData в ядре независимо от статуса)
    elif "text/html" in resp.headers.get("content-type", ""):
        html_text = resp.text
        if target_eid:
            await bot_manager.prepare_presession(target_eid, html_text, req_cookies, page_status=resp.status_code)

        injected_html = _inject_hud(html_text, resp.status_code)
        response = HTMLResponse(content=injected_html, status_code=resp.status_code, headers=resp_headers)


    # 3. Все прочие статические/API ответы
    else:
        response = Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=resp_headers,
            media_type=resp.headers.get("content-type"),
        )

    # Добавление санированных set-cookie
    is_https = request.url.scheme == "https"
    for cookie_hdr in resp.headers.get_list("set-cookie"):
        sanitized = sanitize_set_cookie(cookie_hdr, is_secure=is_https)
        if sanitized:
            response.headers.append("set-cookie", sanitized)

    return response
