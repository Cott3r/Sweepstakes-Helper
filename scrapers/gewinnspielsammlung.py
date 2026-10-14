"""Scraper for gewinnspielsammlung.at - premier Austrian sweepstakes aggregator."""

import hashlib
import re
from datetime import datetime
from typing import List, Optional
from urllib.parse import parse_qs, urlparse, unquote
from bs4 import BeautifulSoup

from models.sweepstake import SweepstakeItem, SweepstakeStatus
from scrapers.base import BaseScraper
from utils.logger import logger


class GewinnspielsammlungScraper(BaseScraper):
    """Scrapes daily verified Austrian sweepstakes from gewinnspielsammlung.at."""

    BASE_URL = "https://www.gewinnspielsammlung.at/"

    def __init__(self):
        super().__init__(name="gewinnspielsammlung_at")

    def _resolve_real_url(self, raw_url: str) -> str:
        """Extracts direct destination URL if wrapped in an affiliate or tracking link."""
        if not raw_url:
            return ""

        # Awin tracking links: &ued=https%3A%2F%2F...
        if "awin1.com" in raw_url or "adcell" in raw_url or "tidd.ly" in raw_url:
            parsed = urlparse(raw_url)
            query = parse_qs(parsed.query)
            if "ued" in query:
                return unquote(query["ued"][0])
            if "url" in query:
                return unquote(query["url"][0])

        return raw_url

    def _parse_deadline(self, text: str) -> Optional[str]:
        """Extracts and formats expiry date from '(Ablauf: DD.MM.YYYY)'."""
        match = re.search(r"Ablauf:\s*(\d{1,2}\s*\.\s*\d{1,2}\s*\.\s*\d{4})", text, re.IGNORECASE)
        if not match:
            return None

        clean_date_str = re.sub(r"\s+", "", match.group(1))
        try:
            dt = datetime.strptime(clean_date_str, "%d.%m.%Y")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return clean_date_str

    def _is_expired(self, deadline_iso: Optional[str]) -> bool:
        """Checks if a deadline has already passed."""
        if not deadline_iso:
            return False
        try:
            today_iso = datetime.now().strftime("%Y-%m-%d")
            return deadline_iso < today_iso
        except Exception:
            return False

    def _generate_external_id(self, url: str, title: str) -> str:
        """Generates a stable unique hash identifier for the sweepstake."""
        key = f"{url.strip().lower()}|{title.strip().lower()}"
        return f"gss_{hashlib.md5(key.encode('utf-8')).hexdigest()[:16]}"

    async def scrape(self) -> List[SweepstakeItem]:
        """Fetches and parses sweepstakes from gewinnspielsammlung.at."""
        logger.info(f"[{self.name}] Fetching listings from {self.BASE_URL}...")
        html = await self.fetch_with_backoff(self.BASE_URL, encoding="iso-8859-1")
        if not html:
            logger.error(f"[{self.name}] Could not retrieve page content.")
            return []

        soup = BeautifulSoup(html, "html.parser")
        items: List[SweepstakeItem] = []

        # Find the container table holding listings
        paragraphs = soup.find_all("p")
        logger.debug(f"[{self.name}] Analyzing {len(paragraphs)} paragraph elements...")

        for p in paragraphs:
            text = p.get_text(" ", strip=True)
            if not ("Ablauf:" in text or "Preis:" in text):
                continue

            a_tag = p.find("a", href=True)
            if not a_tag:
                continue

            raw_href = a_tag["href"].strip()
            if not raw_href.startswith("http") or "gewinnspielsammlung.at" in raw_href:
                continue

            destination_url = self._resolve_real_url(raw_href)

            # Extract sponsor/host name from anchor or prefix text
            host_name = a_tag.get_text(strip=True)

            # Extract prize description
            prize_match = re.search(r"Preis:\s*([^|\(\n\r]+)", text)
            prize_text = prize_match.group(1).strip() if prize_match else ""

            # Full title
            title = f"{host_name} - {prize_text}" if prize_text else (host_name or "Gewinnspiel")

            # Extract deadline
            deadline_iso = self._parse_deadline(text)
            if self._is_expired(deadline_iso):
                logger.debug(f"[{self.name}] Skipping expired sweepstake: {title} ({deadline_iso})")
                continue

            # Extract quiz solution if present
            # Pattern: Lösungsvorschlag: [answer] (Ablauf: ...)
            sol_match = re.search(r"Lösungsvorschlag:?\s*([^|\(\n\r]+)", text)
            quiz_solution = sol_match.group(1).strip() if sol_match else None

            # Detect manual intervention requirements:
            # - Social media action (Instagram, Facebook, TikTok)
            # - Mobile app download (e.g. Mein A1 App, Hofer App)
            # - Creative contest (e.g. Foto hochladen, persönlicher Kommentar)
            # - Pure discount voucher codes (Aktion: -20%)
            text_lower = text.lower()
            requires_manual = False
            if any(term in text_lower for term in [
                "instagram", "facebook", "tiktok", "app herunterladen",
                "foto hochladen", "rezept", "backstage club", "motiv zusammensetzen"
            ]):
                requires_manual = True

            # If it is only a discount promo code without a raffle, skip
            if "gutscheincode:" in text_lower and "preis:" not in text_lower:
                continue

            # Check if recurring daily contest (e.g. daily quiz or advent calendar)
            is_daily = any(term in text_lower for term in [
                "täglich", "jeden tag", "adventkalender", "adventskalender", "tagespreis"
            ])

            external_id = self._generate_external_id(destination_url, title)

            item = SweepstakeItem(
                source=self.name,
                external_id=external_id,
                url=destination_url,
                title=title,
                deadline=deadline_iso,
                eligibility_country="AT",
                requires_manual_entry=requires_manual,
                status=SweepstakeStatus.PENDING,
                is_daily_recurring=is_daily,
                quiz_answer=quiz_solution,
            )
            items.append(item)

        logger.info(f"[{self.name}] Successfully extracted {len(items)} eligible sweepstakes.")
        return items
