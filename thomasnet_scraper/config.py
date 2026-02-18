"""Constants: URLs, CSS selectors, rate-limiting, DB path."""

import os

# ── URLs ──────────────────────────────────────────────────────────────
BASE_URL = "https://www.thomasnet.com"
SEARCH_PATH = "/suppliers/search"
SEARCH_URL = f"{BASE_URL}{SEARCH_PATH}"

# ── Query-string defaults ─────────────────────────────────────────────
DEFAULT_WHAT = ""          # keyword filled at runtime
DEFAULT_HEADING = ""       # optional NAICS/heading filter
DEFAULT_PAGE = 1
RESULTS_PER_PAGE = 30      # ThomasNet default

# ── CSS selectors ────────────────────────────────────────────────────
# ThomasNet uses CSS-modules with hashed suffixes (e.g. _supplierName__Uw5Cu).
# We use [class*=...] substring matchers so selectors survive hash changes.
#
# Supplier card container
SEL_SUPPLIER_CARD = "[class*=search-result-supplier_searchResultSupplierPanel]"
# Inside a card
SEL_COMPANY_NAME = "[class*=supplier-name-link_supplierName]"
SEL_LOCATION = "[class*=supplier-card-header_supplierInfoColumn]"
SEL_PHONE = "a[href^='tel:']"
SEL_WEBSITE = "[class*=supplier-card-header_visitWebsiteButton]"
SEL_DESCRIPTION = "[class*=supplier-catalog_supplierCatalog]"
SEL_COMPANY_TYPE = "[class*=supplier-badge]"
# Detail items (tags like revenue, employees, year founded)
SEL_DETAIL_ITEMS = "[class*=supplier-tags_tagsContainer] span"
# Pagination / total count
SEL_TOTAL_SUPPLIERS = "[class*=search-results_searchHeading]"
SEL_NEXT_PAGE = "[class*=search-results_pagination] a[aria-label='Next']"
# Data attribute on the card that holds impression-tracking JSON
ATTR_IMPRESSION = "data-impression-tracking"

# ── Rate limiting / timeouts ──────────────────────────────────────────
MIN_DELAY = 2          # seconds between page loads
MAX_DELAY = 5
PAGE_TIMEOUT = 30_000  # milliseconds (Playwright)
MAX_RETRIES = 3
BACKOFF_BASE = 2       # exponential backoff multiplier

# ── Database ──────────────────────────────────────────────────────────
DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "thomasnet.db",
)

# ── Browser ───────────────────────────────────────────────────────────
VIEWPORT = {"width": 1920, "height": 1080}
