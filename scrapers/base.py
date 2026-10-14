"""Base scraper class providing randomized delays, user-agent rotation, and exponential backoff."""

import abc
import asyncio
import random
from typing import List, Optional
import httpx

from config.settings import settings
from models.sweepstake import SweepstakeItem
from utils.logger import logger

# Pool of realistic modern browser user agents
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:129.0) Gecko/20100101 Firefox/129.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.6; rv:128.0) Gecko/20100101 Firefox/128.0",
]


class BaseScraper(abc.ABC):
    """Abstract base class for all sweepstake scrapers."""

    def __init__(self, name: str):
        self.name = name

    def get_random_user_agent(self) -> str:
        """Selects a random User-Agent string from the pool."""
        return random.choice(USER_AGENTS)

    async def random_delay(self, min_s: Optional[float] = None, max_s: Optional[float] = None) -> None:
        """Pauses execution for a randomized jitter duration to mimic human pacing.

        Args:
            min_s: Minimum delay in seconds (defaults to settings.MIN_REQUEST_DELAY).
            max_s: Maximum delay in seconds (defaults to settings.MAX_REQUEST_DELAY).
        """
        lower = min_s if min_s is not None else settings.MIN_REQUEST_DELAY
        upper = max_s if max_s is not None else settings.MAX_REQUEST_DELAY
        delay = random.uniform(lower, upper)
        logger.debug(f"[{self.name}] Sleeping for {delay:.2f}s...")
        await asyncio.sleep(delay)

    async def fetch_with_backoff(
        self,
        url: str,
        headers: Optional[dict] = None,
        timeout: float = 15.0,
        encoding: Optional[str] = None
    ) -> Optional[str]:
        """Fetches a URL with exponential backoff on transient errors and status 429/5xx.

        Args:
            url: Target URL to fetch.
            headers: Optional HTTP headers dictionary.
            timeout: Request timeout in seconds.
            encoding: Optional character encoding override.

        Returns:
            Response text if successful, or None if failed after retries.
        """
        merged_headers = {
            "User-Agent": self.get_random_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "de-AT,de;q=0.9,en-US;q=0.8,en;q=0.7",
            "DNT": "1",
        }
        if headers:
            merged_headers.update(headers)

        retries = 0
        backoff = settings.BACKOFF_FACTOR

        while retries <= settings.MAX_RETRIES:
            try:
                await self.random_delay()
                async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
                    response = await client.get(url, headers=merged_headers)

                    if response.status_code == 200:
                        if encoding:
                            response.encoding = encoding
                        return response.text

                    if response.status_code in (429, 500, 502, 503, 504):
                        retries += 1
                        sleep_time = (backoff ** retries) + random.uniform(0.5, 1.5)
                        logger.warning(
                            f"[{self.name}] Received status {response.status_code} for {url}. "
                            f"Retrying in {sleep_time:.2f}s (Attempt {retries}/{settings.MAX_RETRIES})..."
                        )
                        await asyncio.sleep(sleep_time)
                        continue

                    logger.error(f"[{self.name}] Failed to fetch {url} with status {response.status_code}")
                    return None

            except (httpx.RequestError, httpx.TimeoutException) as exc:
                retries += 1
                sleep_time = (backoff ** retries) + random.uniform(0.5, 1.5)
                logger.warning(
                    f"[{self.name}] Network error for {url}: {exc}. "
                    f"Retrying in {sleep_time:.2f}s (Attempt {retries}/{settings.MAX_RETRIES})..."
                )
                await asyncio.sleep(sleep_time)

        logger.error(f"[{self.name}] Exceeded maximum retries for {url}")
        return None

    @abc.abstractmethod
    async def scrape(self) -> List[SweepstakeItem]:
        """Runs scraper and returns a list of discovered SweepstakeItem objects."""
        pass
