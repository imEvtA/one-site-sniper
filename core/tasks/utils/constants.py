EVENT_ID = "47425"
EVENT_URL = "https://www.ticketpro.by/kupit-bilet"
SCHEME_API_URL = "https://auth.ticketpro.by/ticket-api/v1/get-scheme-prices-grouped"
POST_URL = "https://www.ticketpro.by/api/ticket/ticket-reserve/"


HEADERS_TEMPLATE = {
    "X-CSRF-Token": "",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/json",
    "Referer": f"{EVENT_URL}/{EVENT_ID}/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ..."
}

PARAMS_TEMPLATE = {
    "ticket_id": "",
    "price_id": "",
    "count": 1
}