"""Logging, delays, and graceful shutdown."""

from __future__ import annotations

import asyncio
import logging
import random
import signal

from directindustry_scraper.config import MIN_DELAY, MAX_DELAY


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )


async def polite_delay(min_s: float = MIN_DELAY, max_s: float = MAX_DELAY) -> None:
    delay = random.uniform(min_s, max_s)
    logging.getLogger(__name__).debug("Waiting %.1fs", delay)
    await asyncio.sleep(delay)


class GracefulShutdown:
    """Sets a flag on SIGINT/SIGTERM so the scraper can finish the current page."""

    def __init__(self) -> None:
        self.should_stop = False
        self._original_handlers: dict[int, object] = {}

    def install(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            self._original_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, self._handler)

    def _handler(self, signum: int, frame: object) -> None:
        logging.getLogger(__name__).warning(
            "Shutdown signal received — finishing current page then stopping."
        )
        self.should_stop = True

    def uninstall(self) -> None:
        for sig, handler in self._original_handlers.items():
            signal.signal(sig, handler)
        self._original_handlers.clear()
