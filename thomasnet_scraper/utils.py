"""Logging, delays, state parsing, graceful shutdown."""

from __future__ import annotations

import asyncio
import logging
import random
import re
import signal

from thomasnet_scraper.config import MIN_DELAY, MAX_DELAY


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


# US state abbreviations
_US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC", "PR",
}


def parse_state_from_location(location: str | None) -> str | None:
    """Extract US state abbreviation from a location string like 'City, ST'."""
    if not location:
        return None
    match = re.search(r",\s*([A-Z]{2})\b", location)
    if match and match.group(1) in _US_STATES:
        return match.group(1)
    return None


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
