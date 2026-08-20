# Target Platform Configuration & Endpoints

EVENT_ID = "47425"
TARGET_HOST = "https://www.ticketpro.by"
TARGET_DOMAIN = "ticketpro.by"
TARGET_HOST_HEADER = "www.ticketpro.by"

EVENT_URL = f"{TARGET_HOST}/kupit-bilet"
SCHEME_API_URL = "https://auth.ticketpro.by/ticket-api/v1/get-scheme-prices-grouped"
POST_URL = f"{TARGET_HOST}/api/ticket/ticket-reserve/"
AUTH_SVG_BASE_URL = "https://auth.ticketpro.by/ticket/file"

HEADERS_TEMPLATE = {
    "X-CSRF-Token": "",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/json",
    "Referer": f"{EVENT_URL}/{EVENT_ID}/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

PARAMS_TEMPLATE = {
    "ticket_id": "",
    "price_id": "",
    "count": 1,
}