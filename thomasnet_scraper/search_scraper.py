"""Core search scraper: navigate, paginate, extract supplier cards."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from typing import Optional
from urllib.parse import urlencode

from patchright.async_api import Page, TimeoutError as PlaywrightTimeout

from thomasnet_scraper.browser import BrowserManager
from thomasnet_scraper.config import (
    ATTR_IMPRESSION,
    BACKOFF_BASE,
    BASE_URL,
    MAX_RETRIES,
    PAGE_TIMEOUT,
    RESULTS_PER_PAGE,
    SEARCH_URL,
    SEL_COMPANY_NAME,
    SEL_COMPANY_TYPE,
    SEL_DESCRIPTION,
    SEL_DETAIL_ITEMS,
    SEL_LOCATION,
    SEL_PHONE,
    SEL_SUPPLIER_CARD,
    SEL_TOTAL_SUPPLIERS,
    SEL_WEBSITE,
)
from thomasnet_scraper.db import Database
from thomasnet_scraper.models import Supplier
from thomasnet_scraper.utils import GracefulShutdown, parse_state_from_location, polite_delay

log = logging.getLogger(__name__)

# How long to wait for a human to solve a CAPTCHA in --no-headless mode
CAPTCHA_WAIT_TIMEOUT = 120  # seconds


def _build_search_url(keyword: str, heading: Optional[str], page: int) -> str:
    params: dict[str, str | int] = {"searchterm": keyword, "pg": page}
    if heading:
        params["heading"] = heading
    return f"{SEARCH_URL}?{urlencode(params)}"


async def _is_challenge_page(page: Page) -> bool:
    """Detect DataDome/Cloudflare CAPTCHA challenge pages."""
    content = await page.content()
    markers = ("captcha-delivery.com", "geo.captcha-delivery", "var dd=")
    return len(content) < 5000 and any(m in content for m in markers)


async def _wait_for_challenge(page: Page, headless: bool) -> bool:
    """Wait for a bot challenge to be resolved.

    In headed mode, prompts the user to solve the CAPTCHA manually.
    Returns True if the page loaded successfully after the challenge.
    """
    if not await _is_challenge_page(page):
        return True

    if headless:
        log.error(
            "Bot challenge detected (CAPTCHA). Re-run with --no-headless "
            "to solve it manually in the browser window."
        )
        return False

    log.warning(
        "Bot challenge detected! Please solve the CAPTCHA in the browser window. "
        "Waiting up to %ds...",
        CAPTCHA_WAIT_TIMEOUT,
    )
    elapsed = 0
    while elapsed < CAPTCHA_WAIT_TIMEOUT:
        await asyncio.sleep(3)
        elapsed += 3
        if not await _is_challenge_page(page):
            log.info("Challenge solved! Continuing...")
            return True

    log.error("Timed out waiting for CAPTCHA to be solved.")
    return False


async def _get_text(el) -> Optional[str]:
    """Get text from an element, handling SVG/non-HTML nodes gracefully."""
    if el is None:
        return None
    try:
        text = await el.text_content()
        return text.strip() if text else None
    except Exception:
        return None


async def _extract_card(card, search_term: str) -> Optional[Supplier]:
    """Extract supplier data from a card element using JS evaluation."""
    try:
        # Extract all data at once via JS to avoid multiple round-trips
        data = await card.evaluate("""(el) => {
            const q = (sel) => el.querySelector(sel);
            const nameEl = q("[class*=supplier-name-link_supplierName]") ||
                           q("[class*=supplierName]") || q("h2 a") || q("a h2");
            const locEl = q("[class*=supplier-card-header_supplierInfoColumn]") ||
                          q("[class*=supplierInfoColumn]");
            const phoneEl = q("a[href^='tel:']");
            const webEl = q("[class*=visitWebsiteButton]") ||
                          q("a[class*=visitWebsite]");
            const descEl = q("[class*=supplier-catalog_supplierCatalog]") ||
                           q("[class*=supplierCatalog]");
            const badgeEl = q("[class*=supplier-badge]");

            // Get impression tracking
            let companyId = null;
            const impAttr = el.getAttribute("data-impression-tracking");
            if (impAttr) {
                try {
                    const imp = JSON.parse(impAttr);
                    companyId = String(imp.companyId || imp.id || "");
                } catch(e) {}
            }

            // Determine link element for name/URL
            let nameLink = null;
            if (nameEl) {
                nameLink = nameEl.tagName === "A" ? nameEl :
                           nameEl.querySelector("a") || nameEl.closest("a");
            }

            return {
                companyId: companyId,
                companyName: nameEl ? nameEl.textContent.trim() : null,
                profileUrl: nameLink ? nameLink.getAttribute("href") : null,
                location: locEl ? locEl.textContent.trim() : null,
                phone: phoneEl ? phoneEl.textContent.trim() : null,
                phoneHref: phoneEl ? phoneEl.getAttribute("href") : null,
                website: webEl ? webEl.getAttribute("href") : null,
                description: descEl ? descEl.textContent.trim() : null,
                badge: badgeEl ? badgeEl.textContent.trim() : null,
            };
        }""")

        company_name = data.get("companyName")
        if not company_name:
            return None

        company_id = data.get("companyId") or ""
        if not company_id:
            company_id = re.sub(r"\W+", "-", company_name.lower()).strip("-")

        location_raw = data.get("location")
        # Location column may contain multiple lines (location, phone, etc.)
        # Try to extract just the city/state line
        location = None
        if location_raw:
            for line in location_raw.split("\n"):
                line = line.strip()
                if re.search(r"[A-Z]{2}", line) and "," in line:
                    location = line
                    break
            if not location:
                location = location_raw.split("\n")[0].strip()

        state = parse_state_from_location(location)

        phone = data.get("phone")
        if not phone and data.get("phoneHref"):
            phone = data["phoneHref"].replace("tel:", "")

        return Supplier(
            company_id=company_id,
            company_name=company_name,
            search_term=search_term,
            location=location,
            state=state,
            phone=phone,
            website=data.get("website"),
            profile_url=data.get("profileUrl"),
            description=data.get("description"),
            company_type=data.get("badge"),
        )
    except Exception as exc:
        log.warning("Failed to extract card: %s", exc)
        return None


async def _parse_total_suppliers(page: Page) -> Optional[int]:
    """Extract total supplier count from the results header or sort/show text."""
    try:
        # Try multiple selectors for the count text
        for sel in [
            "[class*=search-sort_showResults]",
            "[class*=search-results_searchHeading]",
            "[class*=searchResultsHeader]",
        ]:
            el = await page.query_selector(sel)
            if el:
                text = (await el.inner_text()).strip()
                log.debug("Count element (%s) text: %r", sel, text[:200])
                # Match "1-30 of 542" or "Showing 542 results" or just "542"
                match = re.search(r"of\s+([\d,]+)", text)
                if match:
                    return int(match.group(1).replace(",", ""))
                match = re.search(r"([\d,]+)\s*(?:results?|suppliers?)", text, re.IGNORECASE)
                if match:
                    return int(match.group(1).replace(",", ""))
                digits = re.sub(r"[^\d]", "", text)
                if digits:
                    return int(digits)

        # Fallback: count cards on first page
        cards = await page.query_selector_all(SEL_SUPPLIER_CARD)
        if cards:
            count = len(cards)
            log.info("No total count found — using card count on page 1: %d", count)
            return count
    except Exception as exc:
        log.debug("Error parsing total suppliers: %s", exc)
    return None


async def _navigate_with_retry(
    page: Page, url: str, *, headless: bool = True
) -> bool:
    """Navigate to URL with exponential backoff retries and challenge handling."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await page.goto(url, timeout=PAGE_TIMEOUT, wait_until="domcontentloaded")
            if resp and resp.status == 403:
                # Check if it's a solvable challenge
                if await _is_challenge_page(page):
                    if await _wait_for_challenge(page, headless):
                        return True
                    return False
                log.warning("Got 403 on attempt %d for %s", attempt, url)
                if attempt < MAX_RETRIES:
                    wait = BACKOFF_BASE ** attempt
                    log.info("Backing off %.0fs before retry", wait)
                    await polite_delay(wait, wait + 1)
                    continue
                return False
            return True
        except PlaywrightTimeout:
            log.warning("Timeout on attempt %d for %s", attempt, url)
            if attempt < MAX_RETRIES:
                wait = BACKOFF_BASE ** attempt
                await polite_delay(wait, wait + 1)
        except Exception as exc:
            log.error("Navigation error on attempt %d: %s", attempt, exc)
            if attempt < MAX_RETRIES:
                wait = BACKOFF_BASE ** attempt
                await polite_delay(wait, wait + 1)
    return False


async def scrape_search(
    keyword: str,
    *,
    heading: Optional[str] = None,
    max_pages: Optional[int] = None,
    headless: bool = True,
    db_path: Optional[str] = None,
) -> None:
    """Main entry: search ThomasNet for a keyword and store results."""
    from thomasnet_scraper.config import DEFAULT_DB_PATH

    db = Database(db_path or DEFAULT_DB_PATH)
    shutdown = GracefulShutdown()
    shutdown.install()

    try:
        # Check for resumable session
        existing = db.get_resumable_session(keyword, heading)
        if existing:
            session_id = existing["id"]
            log.info(
                "Resuming session %d (page %d/%s)",
                session_id,
                existing["last_page"],
                existing["total_pages"] or "?",
            )
        else:
            session_id = db.create_session(keyword, heading)
            log.info("Created new session %d for '%s'", session_id, keyword)

        async with BrowserManager(headless=headless) as bm:
            page = await bm.new_page()

            # Visit homepage first to establish cookies/session
            log.info("Warming up: visiting homepage")
            try:
                await page.goto(BASE_URL, timeout=PAGE_TIMEOUT, wait_until="domcontentloaded")
                # Handle challenge on homepage
                if not await _wait_for_challenge(page, headless):
                    db.update_session(session_id, status="failed")
                    return
                await polite_delay()
            except Exception as exc:
                log.warning("Homepage warmup failed (continuing): %s", exc)

            # Navigate to first page to get total count (only if new session)
            if not existing or existing["total_pages"] is None:
                url = _build_search_url(keyword, heading, 1)
                log.info("Navigating to %s", url)
                if not await _navigate_with_retry(page, url, headless=headless):
                    log.error("Failed to load search results page")
                    db.update_session(session_id, status="failed")
                    return

                # Wait for cards or a "no results" indicator
                try:
                    await page.wait_for_selector(
                        f"{SEL_SUPPLIER_CARD}, .no-results",
                        timeout=PAGE_TIMEOUT,
                    )
                except PlaywrightTimeout:
                    # Diagnose what's on the page
                    cur_url = page.url
                    content = await page.content()
                    log.error(
                        "Timed out waiting for search results. "
                        "URL=%s, content_len=%d, is_challenge=%s",
                        cur_url, len(content),
                        len(content) < 5000 and "captcha" in content.lower(),
                    )
                    if len(content) > 5000:
                        # Dump class names for debugging
                        classes = await page.evaluate(
                            """() => {
                            const s = new Set();
                            document.querySelectorAll('*').forEach(el =>
                                el.classList.forEach(c => s.add(c))
                            );
                            return [...s].filter(c =>
                                /supplier|card|result|company|profile|search|listing/i.test(c)
                            ).sort();
                            }"""
                        )
                        log.debug("Relevant CSS classes on page: %s", classes)
                    else:
                        log.debug("Page content (truncated): %s", content[:500])
                    db.update_session(session_id, status="failed")
                    return

                total = await _parse_total_suppliers(page)
                log.debug("Parsed total suppliers: %s", total)
                if total is None or total == 0:
                    # Debug: what URL did we end up on?
                    log.debug("Final URL: %s", page.url)
                    log.warning("No suppliers found for '%s'", keyword)
                    db.update_session(session_id, status="completed", total_suppliers=0)
                    return

                total_pages = math.ceil(total / RESULTS_PER_PAGE)
                if max_pages:
                    total_pages = min(total_pages, max_pages)

                db.update_session(
                    session_id,
                    total_pages=total_pages,
                    total_suppliers=total,
                )
                db.ensure_pages(session_id, total_pages)
                log.info("Found %d suppliers across %d pages", total, total_pages)
            else:
                total_pages = existing["total_pages"]
                if max_pages:
                    total_pages = min(total_pages, max_pages)

            # Scrape pending pages
            pending = db.get_pending_pages(session_id)
            if not pending:
                log.info("All pages already scraped")
                db.update_session(session_id, status="completed")
                return

            for pg_num in pending:
                if shutdown.should_stop:
                    log.info("Graceful shutdown — stopping after page %d", pg_num - 1)
                    db.update_session(session_id, status="interrupted")
                    break

                if max_pages and pg_num > max_pages:
                    break

                url = _build_search_url(keyword, heading, pg_num)
                log.info("Scraping page %d/%d: %s", pg_num, total_pages, url)

                if not await _navigate_with_retry(page, url, headless=headless):
                    db.mark_page_failed(session_id, pg_num, "navigation failed after retries")
                    continue

                try:
                    await page.wait_for_selector(SEL_SUPPLIER_CARD, timeout=PAGE_TIMEOUT)
                except PlaywrightTimeout:
                    db.mark_page_failed(session_id, pg_num, "timeout waiting for cards")
                    continue

                cards = await page.query_selector_all(SEL_SUPPLIER_CARD)
                suppliers: list[Supplier] = []
                for card in cards:
                    supplier = await _extract_card(card, keyword)
                    if supplier:
                        suppliers.append(supplier)

                db.upsert_suppliers(suppliers)
                db.mark_page_done(session_id, pg_num, len(suppliers))
                db.update_session(session_id, last_page=pg_num)
                log.info("Page %d: extracted %d suppliers", pg_num, len(suppliers))

                # Polite delay between pages (skip after last page)
                if pg_num < total_pages and not shutdown.should_stop:
                    await polite_delay()

            # Check final status
            remaining = db.get_pending_pages(session_id)
            if not remaining:
                db.update_session(session_id, status="completed")
                log.info("Scrape completed for '%s'", keyword)
            elif not shutdown.should_stop:
                db.update_session(session_id, status="partial")

    finally:
        shutdown.uninstall()
        total_saved = db.count_suppliers(keyword)
        log.info("Total suppliers in DB for '%s': %d", keyword, total_saved)
        db.close()
