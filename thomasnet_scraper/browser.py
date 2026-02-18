"""Playwright browser lifecycle with anti-detection."""

from __future__ import annotations

import logging
from types import TracebackType
from typing import Optional

from patchright.async_api import Browser, BrowserContext, Page, async_playwright

from thomasnet_scraper.config import VIEWPORT

log = logging.getLogger(__name__)


class BrowserManager:
    """Async context manager that launches Chromium with anti-detection settings.

    Uses patchright (patched Playwright fork) for better stealth.
    When available, uses the system Chrome installation (channel='chrome').
    """

    def __init__(self, headless: bool = True) -> None:
        self.headless = headless
        self._pw = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

    async def __aenter__(self) -> BrowserManager:
        self._pw = await async_playwright().start()

        # Try real Chrome first, fall back to bundled Chromium
        try:
            self._browser = await self._pw.chromium.launch(
                headless=self.headless,
                channel="chrome",
                args=["--disable-blink-features=AutomationControlled"],
            )
            log.debug("Launched system Chrome (headless=%s)", self.headless)
        except Exception:
            self._browser = await self._pw.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            log.debug("Launched bundled Chromium (headless=%s)", self.headless)

        self._context = await self._browser.new_context(
            viewport=VIEWPORT,
            locale="en-US",
            timezone_id="America/New_York",
        )
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        log.debug("Browser closed")

    async def new_page(self) -> Page:
        assert self._context is not None, "BrowserManager not entered"
        return await self._context.new_page()
