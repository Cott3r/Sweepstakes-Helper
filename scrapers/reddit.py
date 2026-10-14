"""Scraper for r/sweepstakes with strict Austria/EU/Worldwide eligibility filtering and dual HTTP/Playwright fetching."""

import re
from datetime import datetime
from typing import List, Optional, Tuple
import httpx
from playwright.async_api import async_playwright

from config.settings import settings
from models.sweepstake import SweepstakeItem, SweepstakeStatus
from scrapers.base import BaseScraper
from utils.logger import logger


class RedditSweepstakesScraper(BaseScraper):
    """Scrapes r/sweepstakes, applying geo-filtering for Austria (AT), EU, and Worldwide (WW)."""

    REDDIT_JSON_URL = "https://www.reddit.com/r/sweepstakes/new.json?limit=50"
    REDDIT_RSS_URL = "https://www.reddit.com/r/sweepstakes/new.rss"
    REDDIT_WEB_URL = "https://www.reddit.com/r/sweepstakes/new/"

    def __init__(self):
        super().__init__(name="r_sweepstakes")

    def check_eligibility(self, title: str) -> Tuple[bool, str]:
        """Checks whether a sweepstakes post title matches Austria eligibility.

        Rules:
        - Keep if explicitly tagged with [WW], {WW}, [EU], {EU}, [AT], {AT}, [Worldwide], {Worldwide}, [Austria].
        - Reject if strictly tagged with [US], [CA], [UK], [GB], [AU], [NZ], etc., and not including WW/EU/AT.

        Returns:
            Tuple of (is_eligible: bool, matched_country_tag: str)
        """
        # Extract all bracketed/braced tokens: [US], {WW}, (EU), [US/CA], etc.
        geo_tokens = re.findall(r"[\{\[\(]([A-Za-z\s\/\,\-]+)[\}\]\)]", title)

        # Explicit positive tags for Austrian residents
        positive_patterns = [
            r"\bWW\b", r"\bWORLDWIDE\b", r"\bGLOBAL\b",
            r"\bEU\b", r"\bEUROPE\b",
            r"\bAT\b", r"\bAUSTRIA\b", r"\bÖSTERREICH\b",
            r"\bDACH\b",
        ]

        # Explicit negative tags (regions excluding Austria)
        negative_patterns = [
            r"\bUS\b", r"\bUSA\b", r"\bUNITED STATES\b",
            r"\bCA\b", r"\bCANADA\b",
            r"\bUK\b", r"\bGB\b", r"\bUNITED KINGDOM\b",
            r"\bAU\b", r"\bAUSTRALIA\b",
            r"\bNZ\b", r"\bNEW ZEALAND\b",
            r"\bIN\b", r"\bINDIA\b",
            r"\bPH\b", r"\bPHILIPPINES\b",
            r"\bFR\b", r"\bFRANCE\b",
            r"\bDE ONLY\b",
        ]

        title_upper = title.upper()

        # Check positive match first
        for pat in positive_patterns:
            if re.search(pat, title_upper):
                if re.search(r"\b(AT|AUSTRIA|ÖSTERREICH)\b", title_upper):
                    return True, "AT"
                if re.search(r"\b(EU|EUROPE|DACH)\b", title_upper):
                    return True, "EU"
                return True, "WW"

        # Check if strictly negative
        for token in geo_tokens:
            token_upper = token.upper()
            is_neg = any(re.search(neg, token_upper) for neg in negative_patterns)
            is_pos = any(re.search(pos, token_upper) for pos in positive_patterns)
            if is_neg and not is_pos:
                return False, "NON_AT"

        # If title mentions US, CA, UK without positive tag, reject
        for neg in [r"\bUS\b", r"\bUSA\b", r"\bCANADA\b", r"\bUK\b"]:
            if re.search(neg, title_upper):
                return False, "NON_AT"

        # If no geo tag at all, on r/sweepstakes untagged posts are usually ambiguous.
        # However, if it links to Gleam/Sweepwidget without US restriction, we mark as ambiguous WW
        return False, "UNKNOWN"

    def _parse_deadline(self, title: str) -> Optional[str]:
        """Parses common expiration date formats from title, e.g. (MM/DD/YYYY) or (M/D/YY)."""
        match = re.search(r"[\(\[\{](\d{1,2}\/\d{1,2}\/\d{2,4})[\)\]\}]", title)
        if not match:
            return None

        date_str = match.group(1)
        for fmt in ("%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y"):
            try:
                dt = datetime.strptime(date_str, fmt)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                pass
        return None

    def _is_daily(self, title: str) -> bool:
        """Determines if the sweepstakes is a recurring daily entry."""
        title_lower = title.lower()
        return any(term in title_lower for term in [
            "daily", "day 1", "day 2", "24 hours", "every day", "recur"
        ])

    async def _fetch_via_http(self) -> List[dict]:
        """Tries fetching recent posts from Reddit JSON API."""
        headers = {
            "User-Agent": "android:com.sweepstakes_helper_at:v1.0.0 (by /u/austria_bot)",
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                res = await client.get(self.REDDIT_JSON_URL, headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    children = data.get("data", {}).get("children", [])
                    raw_posts = []
                    for c in children:
                        d = c.get("data", {})
                        raw_posts.append({
                            "id": d.get("id"),
                            "title": d.get("title", ""),
                            "url": d.get("url", ""),
                        })
                    logger.info(f"[{self.name}] Successfully fetched {len(raw_posts)} posts via JSON API.")
                    return raw_posts
        except Exception as exc:
            logger.debug(f"[{self.name}] HTTP JSON request failed: {exc}")

        return []

    async def _fetch_via_playwright(self) -> List[dict]:
        """Fallback stealth browser scraper to extract r/sweepstakes posts when API is rate-limited."""
        logger.info(f"[{self.name}] Launching Playwright stealth scraper for Reddit...")
        raw_posts = []
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
                )
                context = await browser.new_context(
                    user_agent=self.get_random_user_agent(),
                    locale="de-AT",
                    viewport={"width": 1280, "height": 800}
                )
                page = await context.new_page()
                await page.goto(self.REDDIT_WEB_URL, wait_until="domcontentloaded", timeout=25000)
                await page.wait_for_timeout(2500)

                # Query shreddit-post elements
                elements = await page.query_selector_all("shreddit-post")
                for el in elements:
                    post_id = await el.get_attribute("id")
                    title = await el.get_attribute("post-title")
                    content_url = await el.get_attribute("content-href")

                    if post_id and title and content_url:
                        clean_id = post_id.replace("t3_", "")
                        raw_posts.append({
                            "id": clean_id,
                            "title": title.strip(),
                            "url": content_url.strip(),
                        })

                await browser.close()
                logger.info(f"[{self.name}] Playwright extracted {len(raw_posts)} posts from Reddit page.")
        except Exception as exc:
            logger.error(f"[{self.name}] Playwright scraper failed on Reddit: {exc}")

        return raw_posts

    async def scrape(self) -> List[SweepstakeItem]:
        """Scrapes and filters eligible sweepstakes for Austrian users."""
        logger.info(f"[{self.name}] Starting r/sweepstakes collection...")

        # 1. Try fast HTTP API first
        raw_posts = await self._fetch_via_http()

        # 2. If API was blocked or returned empty, use Playwright
        if not raw_posts:
            raw_posts = await self._fetch_via_playwright()

        items: List[SweepstakeItem] = []
        for post in raw_posts:
            title = post["title"]
            url = post["url"]
            post_id = post["id"]

            # Filter eligibility
            is_eligible, country_tag = self.check_eligibility(title)
            if not is_eligible:
                logger.debug(f"[{self.name}] Ineligible geo-tag ({country_tag}): {title[:60]}")
                continue

            # Check if redirect or widget platform
            url_lower = url.lower()
            requires_manual = False
            # Gleam, Sweepwidget, ViralSweep, or social tasks may require user authentication or CAPTCHAs
            if any(dom in url_lower for dom in ["gleam.io", "sweepwidget.com", "viralsweep.com", "kingsumo.com"]):
                requires_manual = False  # Will attempt automated form filling, fallback to manual if captcha

            deadline = self._parse_deadline(title)
            is_daily = self._is_daily(title)

            item = SweepstakeItem(
                source=self.name,
                external_id=f"reddit_{post_id}",
                url=url,
                title=title,
                deadline=deadline,
                eligibility_country=country_tag,
                requires_manual_entry=requires_manual,
                status=SweepstakeStatus.PENDING,
                is_daily_recurring=is_daily,
            )
            items.append(item)

        logger.info(f"[{self.name}] Extracted {len(items)} eligible sweepstakes matching AT/EU/WW.")
        return items
