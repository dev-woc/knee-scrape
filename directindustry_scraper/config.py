"""Constants for DirectIndustry scraper."""

import os

# ── URLs ──────────────────────────────────────────────────────────────
BASE_URL = "https://www.directindustry.com"
# Base search URL without query or page
# Pattern: https://www.directindustry.com/find/{query}.html
# Pattern: https://www.directindustry.com/find/{query}-{page}.html

# ── Query-string defaults ─────────────────────────────────────────────
DEFAULT_WHAT = ""
DEFAULT_PAGE = 1
RESULTS_PER_PAGE = 30      # Approximate

# ── CSS selectors ────────────────────────────────────────────────────
# Product/Supplier listing card
SEL_SUPPLIER_CARD = "div.p-item"
# Inside a card
SEL_COMPANY_NAME = "div.manufacturer-name a"
SEL_LOCATION = "div.country-name"
# Direct website link often hidden or redirected
SEL_WEBSITE = "a.visit-website"
# Pagination / total count
SEL_TOTAL_COUNT = "span.nb-products"

# ── Rate limiting / timeouts ──────────────────────────────────────────
MIN_DELAY = 3
MAX_DELAY = 6
PAGE_TIMEOUT = 45_000
MAX_RETRIES = 3
BACKOFF_BASE = 2

# ── Database ──────────────────────────────────────────────────────────
DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "directindustry.db",
)

# ── Browser ───────────────────────────────────────────────────────────
VIEWPORT = {"width": 1920, "height": 1080}
