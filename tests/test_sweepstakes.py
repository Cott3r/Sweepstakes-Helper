"""Comprehensive test suite for Sweepstakes Helper Austria."""

import os
import tempfile
from pathlib import Path
import pytest

from config.settings import load_user_profile, Settings
from database.db import Database
from models.sweepstake import SweepstakeItem, SweepstakeStatus, UserProfile
from scrapers.reddit import RedditSweepstakesScraper
from scrapers.gewinnspielsammlung import GewinnspielsammlungScraper
from automation.form_filler import FormFiller


def test_reddit_eligibility_filter():
    """Verifies geo-tagging rules for Austrian residents."""
    scraper = RedditSweepstakesScraper()

    # Positive matches (Austria eligible)
    assert scraper.check_eligibility("Logitech Gaming Bundle [WW] (12/31/2026)")[0] is True
    assert scraper.check_eligibility("Steam Deck OLED Giveaway {Worldwide} (10/20)")[0] is True
    assert scraper.check_eligibility("Samsung Galaxy S24 Ultra [EU] (11/15/2026)")[0] is True
    assert scraper.check_eligibility("Win a Holiday in Tyrol [AT] (10/30/2026)")[0] is True
    assert scraper.check_eligibility("Gamer Chair DACH [AT/DE/CH]")[0] is True
    assert scraper.check_eligibility("Global RTX 4090 Giveaway [WW]")[0] is True

    # Negative matches (Restricted to other regions)
    assert scraper.check_eligibility("🎉 WIN $5,000 WORTH OF FREE TOOL GEAR 🎉{US} (8/21/2025)")[0] is False
    assert scraper.check_eligibility("Enter to win a Football Prize Pack (09/12/2025) {US}")[0] is False
    assert scraper.check_eligibility("WIN a $25 Home Depot eGift Card (08/19/2025) {US CA}")[0] is False
    assert scraper.check_eligibility("£500 Tesco Gift Card [UK]")[0] is False
    assert scraper.check_eligibility("Win an Australian Outback Tour [AU]")[0] is False
    assert scraper.check_eligibility("Canadian Coffee Pack [CA only]")[0] is False


def test_user_profile_and_field_matcher():
    """Verifies semantic profile matching and Austrian address handling."""
    profile = UserProfile(
        first_name="Max",
        last_name="Mustermann",
        email="max@example.at",
        phone="+436601234567",
        street="Mariahilfer Straße",
        house_number="101",
        postal_code="1060",
        city="Wien",
        country="Österreich",
        salutation="Herr",
        birth_date="15.06.1990",
    )

    filler = FormFiller(profile, quiz_answer="Mallorca")

    assert filler._match_field_value("txt_vorname", "text") == "Max"
    assert filler._match_field_value("input_lastname", "text") == "Mustermann"
    assert filler._match_field_value("user_email", "email") == "max@example.at"
    assert filler._match_field_value("telefonnummer", "tel") == "+436601234567"
    assert filler._match_field_value("zip_code_plz", "text") == "1060"
    assert filler._match_field_value("city_ort", "text") == "Wien"
    assert filler._match_field_value("gewinnspiel_antwort", "text") == "Mallorca"
    assert filler._match_field_value("loesungsvorschlag", "text") == "Mallorca"


def test_database_persistence_and_deduplication():
    """Tests SQLite state management and deduplication logic."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test.db"
        db = Database(db_path)

        item = SweepstakeItem(
            source="test_source",
            external_id="item_001",
            url="https://example.at/raffle",
            title="Win a Mountain Bike",
            deadline="2026-12-31",
            eligibility_country="AT",
            requires_manual_entry=False,
            status=SweepstakeStatus.PENDING,
            is_daily_recurring=False,
            quiz_answer="Bike",
        )

        # First insert
        assert db.insert_or_update_item(item) is True

        # Second insert should be ignored by deduplication
        assert db.insert_or_update_item(item) is False

        # Verify pending list
        pending = db.get_pending_items(limit=10)
        assert len(pending) == 1
        assert pending[0].external_id == "item_001"

        # Mark completed
        db.mark_completed(pending[0].id)
        assert len(db.get_pending_items(limit=10)) == 0

        # Stats verification
        stats = db.get_stats()
        assert stats["COMPLETED"] == 1
        assert stats["TOTAL"] == 1


def test_daily_recurring_sweepstake_requeuing():
    """Verifies that daily recurring entries can be re-queued on new days."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "recurring_test.db"
        db = Database(db_path)

        recurring_item = SweepstakeItem(
            source="test_source",
            external_id="daily_wheel_01",
            url="https://example.at/daily-wheel",
            title="Daily Lucky Wheel",
            status=SweepstakeStatus.PENDING,
            is_daily_recurring=True,
        )

        db.insert_or_update_item(recurring_item)
        pending = db.get_pending_items()
        item_id = pending[0].id

        # Mark completed today
        db.mark_completed(item_id)
        assert len(db.get_pending_items()) == 0

        # Simulate last attempted date as yesterday
        with db._get_connection() as conn:
            conn.execute(
                "UPDATE sweepstakes SET last_attempted_at = '2026-10-10T10:00:00' WHERE id = ?",
                (item_id,),
            )
            conn.commit()

        # Re-running scraper should re-queue the daily recurring sweepstake
        re_queued = db.insert_or_update_item(recurring_item)
        assert re_queued is True

        pending_requeued = db.get_pending_items()
        assert len(pending_requeued) == 1
        assert pending_requeued[0].id == item_id


def test_captcha_detector_fastly():
    """Verifies Fastly CAPTCHA challenge detection signatures."""
    from automation.captcha_detector import CaptchaDetector
    
    assert "Fastly Challenge / CAPTCHA" in CaptchaDetector.CAPTCHA_SELECTORS
    fastly_selectors = CaptchaDetector.CAPTCHA_SELECTORS["Fastly Challenge / CAPTCHA"]
    assert any("fastly-challenge" in s for s in fastly_selectors)
    assert any("captcha-iframe.html" in s for s in fastly_selectors)


def test_form_filler_quiz_matching():
    """Verifies FormFiller handles quiz solution descriptors."""
    profile = UserProfile(
        first_name="Max",
        last_name="Mustermann",
        email="max@example.at",
        phone="+436601234567",
        street="Mariahilfer Straße",
        house_number="101",
        postal_code="1060",
        city="Wien",
        country="Österreich",
        salutation="Herr",
        birth_date="15.06.1990",
    )
    filler = FormFiller(profile, quiz_answer="ca. 40%")
    assert filler._match_field_value("solutionword", "text") == "ca. 40%"
    assert filler._match_field_value("gewinnfrage", "text") == "ca. 40%"
    assert filler._match_field_value("antwort", "text") == "ca. 40%"

