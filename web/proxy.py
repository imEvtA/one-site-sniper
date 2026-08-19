import logging
import re
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse

from core.bot import bot_manager

logger = logging.getLogger("web.proxy")

router = APIRouter()

TARGET_HOST = "https://www.ticketpro.by"

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
    Удаляет domain=.ticketpro.by и заменяет SameSite=None на SameSite=Lax (без secure),
    чтобы браузеры не отбрасывали куку на http://localhost:8000.
    """
    val = re.sub(r"(?i)\bdomain=[^;]+;?\s*", "", header_value)
    if not is_secure:
        val = re.sub(r"(?i)\bsecure;?\s*", "", val)
        val = re.sub(r"(?i)\bSameSite=None\b", "SameSite=Lax", val)
    return val.strip().rstrip(";")


def extract_event_id(path: str, referer: str = "") -> str | None:
    # On order/checkout pages, NEVER hijack cookies based on referer
    if path.startswith("order/") or path.startswith("basket") or path.startswith("auth/") or path.startswith("korzina"):
        return None

    match = re.search(r"(?:kupit-bilet|events?)/(\d+)", path)
    if match:
        return match.group(1)
    if referer and (path.startswith("api/ticket/") or path.startswith("ticket-api/")):
        match_ref = re.search(r"(?:kupit-bilet|events?)/(\d+)", referer)
        if match_ref:
            return match_ref.group(1)
    return None


@router.get("/korzina")
@router.get("/korzina/")
@router.get("/basket")
@router.get("/basket/")
@router.get("/cart")
@router.get("/cart/")
async def direct_checkout_redirect():
    """
    Напрямую отправляет пользователя на экран оформления заказа (/order/auth/),
    минуя цикл принудительного логина (/auth/login/?basked=1).
    """
    return Response(status_code=302, headers={"Location": "/order/auth/"})


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"])
async def proxy_pass(request: Request, path: str):
    target_url = f"{TARGET_HOST}/{path}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"

    req_cookies = dict(request.cookies)
    referer_hdr = request.headers.get("referer", "")

    # Event-Aware Session Mapping: only for event pages and ticket APIs
    target_eid = extract_event_id(path, referer_hdr)
    if target_eid:
        session = bot_manager.get(target_eid)
        if session and session.config.cookies:
            req_cookies.update(session.config.cookies)
            logger.info(f"[EVENT-AWARE PROXY] Using sniper session cookies for event {target_eid}")

    logger.info(f"[PROXY REQ] {request.method} {request.url.path} | Cookies: {req_cookies}")

    headers = dict(request.headers)
    headers = {k: v for k, v in headers.items() if k.lower() not in EXCLUDE_REQUEST_HEADERS}
    headers["Host"] = "www.ticketpro.by"
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

    resp_headers = {}
    for k, v in resp.headers.items():
        if k.lower() not in EXCLUDE_RESPONSE_HEADERS:
            resp_headers[k] = v

    # Rewrite redirect Location header to keep browser on proxy
    if "location" in resp.headers:
        loc = resp.headers["location"]
        loc_rewritten = loc.replace("https://www.ticketpro.by", "").replace("http://www.ticketpro.by", "")
        if not loc_rewritten:
            loc_rewritten = "/"
        resp_headers["Location"] = loc_rewritten
        logger.info(f"[PROXY REDIRECT] {resp.status_code} -> Location: {loc_rewritten}")

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
            logger.info(
                f"[BASKET CHECK] Tickets in basket: {items_count} | Time remaining: {time_live} | Items: {basket_info.get('items', [])}"
            )
        except Exception as e:
            logger.warning(f"[BASKET CHECK] Could not parse basket json: {e}")

    content_type = resp.headers.get("content-type", "")

    # If response is a redirect (3xx), return directly
    if resp.status_code in (301, 302, 303, 307, 308):
        response = Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=resp_headers,
        )
    elif "text/html" in content_type:
        html_text = resp.text
        v_ts = int(time.time())

        injection = f"""
        <!-- Injected Ticketpro Sniper HUD -->
        <script>window.__TP_PAGE_STATUS__ = {resp.status_code};</script>
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
            headers=resp_headers,
        )
    else:
        response = Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=resp_headers,
            media_type=content_type,
        )



    # Append sanitized set-cookie headers
    for cookie_header in raw_set_cookies:
        sanitized = sanitize_set_cookie(cookie_header, is_secure=is_https)
        if sanitized:
            logger.info(f"[SET-COOKIE SANITIZED] {sanitized}")
            response.headers.append("set-cookie", sanitized)

    return response
