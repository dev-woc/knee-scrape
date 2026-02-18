"""Core search scraper for DirectIndustry."""

from __future__ import annotations

import asyncio
import logging
import math
import re
from typing import Optional

from patchright.async_api import Page, TimeoutError as PlaywrightTimeout

from directindustry_scraper.browser import BrowserManager
from directindustry_scraper.config import (
    BASE_URL,
    MAX_RETRIES,
    PAGE_TIMEOUT,
    RESULTS_PER_PAGE,
    SEL_COMPANY_NAME,
    SEL_LOCATION,
    SEL_SUPPLIER_CARD,
    SEL_TOTAL_COUNT,
    SEL_WEBSITE,
    BACKOFF_BASE,
)
from directindustry_scraper.db import Database
from directindustry_scraper.models import Supplier
from directindustry_scraper.utils import GracefulShutdown, polite_delay

log = logging.getLogger(__name__)

CAPTCHA_WAIT_TIMEOUT = 120


def _slugify(text: str) -> str:
    """Convert text to a URL-friendly slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_{}]+", "-", text)
    return text.strip("-")


def _build_search_url(keyword: str, page: int) -> str:
    slug = _slugify(keyword)
    if page <= 1:
        return f"{BASE_URL}/find/{slug}.html"
    return f"{BASE_URL}/find/{slug}-{page}.html"


async def _is_challenge_page(page: Page) -> bool:
    """Detect Cloudflare/DataDome challenge pages."""
    content = await page.content()
    markers = ("captcha-delivery.com", "geo.captcha-delivery", "var dd=", "cloudflare", "Turnstile")
    # Low content length + markers usually indicate a challenge
    return len(content) < 8000 and any(m in content for m in markers)


async def _wait_for_challenge(page: Page, headless: bool) -> bool:
    if not await _is_challenge_page(page):
        return True

    if headless:
        log.error("Bot challenge detected. Re-run with --no-headless to solve manually.")
        return False

    log.warning("Bot challenge detected! Please solve it in the browser window. Waiting...")
    elapsed = 0
    while elapsed < CAPTCHA_WAIT_TIMEOUT:
        await asyncio.sleep(3)
        elapsed += 3
        if not await _is_challenge_page(page):
            log.info("Challenge solved!")
            return True

    log.error("Timed out waiting for challenge resolution.")
    return False


async def _extract_card(card, search_term: str) -> Optional[Supplier]:
    try:
        # Company name
        name_el = await card.query_selector(SEL_COMPANY_NAME)
        company_name = (await name_el.inner_text()).strip() if name_el else None
        profile_url = await name_el.get_attribute("href") if name_el else None

        if not company_name:
            return None

        # Generate a stable ID
        company_id = _slugify(company_name)

        # Location
        loc_el = await card.query_selector(SEL_LOCATION)
        location = (await loc_el.inner_text()).strip() if loc_el else None

        # Website (might be redirected)
        web_el = await card.query_selector(SEL_WEBSITE)
        website = await web_el.get_attribute("href") if web_el else None

        return Supplier(
            company_id=company_id,
            company_name=company_name,
            search_term=search_term,
            location=location,
            website=website,
            profile_url=profile_url,
        )
    except Exception as exc:
        log.warning("Failed to extract card: %s", exc)
        return None


async def _parse_total_count(page: Page) -> Optional[int]:
    try:
        el = await page.query_selector(SEL_TOTAL_COUNT)
        if el:
            text = (await el.inner_text()).strip()
            # Extract digits from e.g. "1,234 products" or "1234"
            digits = re.sub(r"[^\d]", "", text)
            return int(digits) if digits else None
    except Exception:
        pass
    return None


async def _navigate_with_retry(page: Page, url: str, *, headless: bool = True) -> bool:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await page.goto(url, timeout=PAGE_TIMEOUT, wait_until="domcontentloaded")
            if resp and resp.status == 403:
                if await _wait_for_challenge(page, headless):
                    return True
                return False
            return True
        except PlaywrightTimeout:
            log.warning("Timeout on attempt %d", attempt)
            if attempt < MAX_RETRIES:
                await polite_delay(BACKOFF_BASE**attempt)
        except Exception as exc:
            log.error("Navigation error: %s", exc)
            if attempt < MAX_RETRIES:
                await polite_delay(BACKOFF_BASE**attempt)
    return False


async def scrape_search(
    keyword: str,
    *,
    max_pages: Optional[int] = None,
    headless: bool = True,
    db_path: Optional[str] = None,
) -> None:
    from directindustry_scraper.config import DEFAULT_DB_PATH

    db = Database(db_path or DEFAULT_DB_PATH)
    shutdown = GracefulShutdown()
    shutdown.install()

    try:
        session = db.get_resumable_session(keyword)
        if session:
            session_id = session["id"]
            log.info("Resuming session %d (page %d)", session_id, session["last_page"])
        else:
            session_id = db.create_session(keyword)
            log.info("New session %d for '%s'", session_id, keyword)

        async with BrowserManager(headless=headless) as bm:
            page = await bm.new_page()

            # Warm up
            log.info("Visiting homepage...")
            try:
                await page.goto(BASE_URL, timeout=PAGE_TIMEOUT)
                await _wait_for_challenge(page, headless)
                await polite_delay()
            except Exception:
                pass

            # Initial load to get total pages
            if not session or session["total_pages"] is None:
                url = _build_search_url(keyword, 1)
                log.info("Navigating to %s", url)
                if not await _navigate_with_retry(page, url, headless=headless):
                    db.update_session(session_id, status="failed")
                    return

                total = await _parse_total_count(page)
                if not total:
                    log.warning("No results found.")
                    db.update_session(session_id, status="completed", total_suppliers=0)
                    return

                total_pages = math.ceil(total / RESULTS_PER_PAGE)
                if max_pages:
                    total_pages = min(total_pages, max_pages)

                db.update_session(session_id, total_pages=total_pages, total_suppliers=total)
                db.ensure_pages(session_id, total_pages)
                log.info("Found %d results across %d pages", total, total_pages)
            else:
                total_pages = session["total_pages"]
                if max_pages:
                    total_pages = min(total_pages, max_pages)

            pending = db.get_pending_pages(session_id)
            for pg_num in pending:
                if shutdown.should_stop or (max_pages and pg_num > max_pages):
                    db.update_session(session_id, status="interrupted")
                    break

                url = _build_search_url(keyword, pg_num)
                log.info("Scraping page %d/%d: %s", pg_num, total_pages, url)

                if not await _navigate_with_retry(page, url, headless=headless):
                    db.mark_page_failed(session_id, pg_num, "navigation failed")
                    continue

                try:
                    await page.wait_for_selector(SEL_SUPPLIER_CARD, timeout=PAGE_TIMEOUT)
                except PlaywrightTimeout:
                    db.mark_page_failed(session_id, pg_num, "timeout")
                    continue

                cards = await page.query_selector_all(SEL_SUPPLIER_CARD)
                suppliers = []
                for card in cards:
                    s = await _extract_card(card, keyword)
                    if s:
                        suppliers.append(s)

                db.upsert_suppliers(suppliers)
                db.mark_page_done(session_id, pg_num, len(suppliers))
                db.update_session(session_id, last_page=pg_num)
                log.info("Extracted %d suppliers", len(suppliers))

                if pg_num < total_pages:
                    await polite_delay()

            # Final check
            if not db.get_pending_pages(session_id):
                db.update_session(session_id, status="completed")
                log.info("Done!")

    finally:
        shutdown.uninstall()
        db.close()
