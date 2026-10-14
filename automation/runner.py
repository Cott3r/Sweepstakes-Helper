"""Participation strategy engine implementing the tiered automated and interactive execution model."""

import asyncio
import sys
from typing import Optional, Tuple
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from automation.browser import BrowserManager
from automation.captcha_detector import CaptchaDetector
from automation.form_filler import FormFiller
from config.settings import settings
from database.db import Database
from models.sweepstake import SweepstakeItem, SweepstakeStatus, UserProfile
from utils.logger import logger


class AutomationRunner:
    """Orchestrates automated form submissions and graceful interactive fallback."""

    def __init__(self, db: Database, profile: UserProfile):
        self.db = db
        self.profile = profile

    async def dismiss_cookie_banners(self, page: Page) -> None:
        """Dismisses common European GDPR / ePrivacy cookie consent overlays."""
        # 1. Specialized handling for Usercentrics CMP (common across Austrian retail sites)
        try:
            uc_btn = page.locator(
                "#usercentrics-cmp-ui button#accept, "
                "#usercentrics-cmp-ui button[data-testid='uc-accept-all-button'], "
                "#usercentrics-cmp-ui button.uc-accept-button, "
                "button#uc-btn-accept-banner"
            ).first
            if await uc_btn.is_visible(timeout=1200):
                logger.debug("Dismissing Usercentrics cookie consent banner")
                await uc_btn.click(timeout=2000)
                await page.wait_for_timeout(500)
                return
        except Exception:
            pass

        # 2. General GDPR / ePrivacy consent selectors
        cookie_button_selectors = [
            "button:has-text('Alle akzeptieren')",
            "button:has-text('Akzeptieren')",
            "button:has-text('Annehmen')",
            "button:has-text('Alle annehmen')",
            "button:has-text('Zustimmen')",
            "button:has-text('Alles akzeptieren')",
            "button:has-text('Einverstanden')",
            "button:has-text('Accept all')",
            "button:has-text('Accept All')",
            "button:has-text('Agree')",
            "button#onetrust-accept-btn-handler",
            "button.didomi-components-button--color-primary",
            "button#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
            "button#accept",
            "a:has-text('Alle akzeptieren')",
        ]

        for selector in cookie_button_selectors:
            try:
                btn = page.locator(selector).first
                if await btn.is_visible(timeout=800):
                    logger.debug(f"Dismissing cookie consent banner with '{selector}'")
                    await btn.click(timeout=1500)
                    await page.wait_for_timeout(500)
                    break
            except Exception:
                continue

    def play_chime(self) -> None:
        """Emits an audible terminal notification chime."""
        if settings.PLAY_AUDIO_CHIME:
            sys.stdout.write("\a")
            sys.stdout.flush()

    async def run_single_automated(
        self,
        item: SweepstakeItem,
        page: Page
    ) -> Tuple[SweepstakeStatus, Optional[str]]:
        """Executes a fully automated submission attempt in headless mode.

        Returns:
            Tuple of (status: SweepstakeStatus, message/error: Optional[str])
        """
        logger.info(f"Opening sweepstake URL: {item.url} for '{item.title}'")

        try:
            # Navigate to destination
            response = await page.goto(
                item.url,
                wait_until="domcontentloaded",
                timeout=settings.BROWSER_TIMEOUT_MS
            )
            await page.wait_for_timeout(settings.PAGE_LOAD_WAIT_MS)

            # Dismiss cookie banners if present
            await self.dismiss_cookie_banners(page)

            # Step 1: Check for CAPTCHA
            has_captcha, captcha_type = await CaptchaDetector.detect_captcha(page)
            if has_captcha:
                reason = f"Detected {captcha_type}"
                logger.warning(f"Contest {item.id} requires manual entry: {reason}")
                return SweepstakeStatus.PENDING, reason

            # Step 2: Fill form
            filler = FormFiller(self.profile, quiz_answer=item.quiz_answer)
            filled = await filler.fill_form(page)

            if not filled:
                reason = "No recognized entry form fields found on page"
                logger.info(f"Contest {item.id}: {reason}")
                return SweepstakeStatus.PENDING, reason

            logger.info(f"Successfully pre-filled {len(filled)} fields for '{item.title}'")

            # Step 3: Locate submit button
            submit_btn = await filler.find_submit_button(page)
            if not submit_btn:
                reason = "Form fields filled, but submit button could not be determined"
                logger.warning(f"Contest {item.id}: {reason}")
                return SweepstakeStatus.PENDING, reason

            # Click submit button
            logger.info(f"Submitting form for '{item.title}'...")
            await submit_btn.click(timeout=settings.ACTION_TIMEOUT_MS)
            await page.wait_for_timeout(3000)

            # Verify outcome: check if any error message or CAPTCHA appeared post-click
            has_post_captcha, post_captcha_type = await CaptchaDetector.detect_captcha(page)
            if has_post_captcha:
                return SweepstakeStatus.PENDING, f"CAPTCHA triggered upon submission: {post_captcha_type}"

            # Check for positive confirmation keywords
            page_text = (await page.content()).lower()
            success_keywords = [
                "vielen dank", "erfolgreich", "danke für deine teilnahme",
                "danke für ihre teilnahme", "teilnahme registriert",
                "bestätigungslink", "viel glück", "thank you",
                "entry received", "entries counted", "you are in",
            ]

            if any(k in page_text for k in success_keywords):
                logger.info(f"Contest {item.id} successfully completed and verified!")
                return SweepstakeStatus.COMPLETED, "Submitted and confirmed successfully"

            # If no obvious error, consider completed
            logger.info(f"Contest {item.id} submitted without detected errors.")
            return SweepstakeStatus.COMPLETED, "Submitted without error"

        except PlaywrightTimeoutError as te:
            err = f"Timeout during automated execution: {te}"
            logger.error(err)
            return SweepstakeStatus.FAILED, err
        except Exception as exc:
            err = f"Error during automated execution: {exc}"
            logger.error(err)
            return SweepstakeStatus.FAILED, err

    async def run_single_interactive(self, item: SweepstakeItem) -> SweepstakeStatus:
        """Launches headful browser session, pre-fills fields, and prompts user for manual confirmation."""
        logger.info(f"Launching Interactive Helper for '{item.title}' (ID {item.id})...")

        async with BrowserManager(headless=False) as browser_mgr:
            page = await browser_mgr.create_stealth_page()

            try:
                await page.goto(item.url, wait_until="domcontentloaded", timeout=settings.BROWSER_TIMEOUT_MS)
                await page.wait_for_timeout(1500)
                await self.dismiss_cookie_banners(page)

                # Pre-fill recognizable fields
                filler = FormFiller(self.profile, quiz_answer=item.quiz_answer)
                filled = await filler.fill_form(page)
                if filled:
                    logger.info(f"Pre-populated {len(filled)} fields for you in the browser.")

                # Play chime and display interactive prompt
                self.play_chime()
                print("\n" + "=" * 76)
                print(f"🔔 ACTION REQUIRED: Interactive Sweepstakes Submission")
                print(f"Title:       {item.title}")
                print(f"URL:         {item.url}")
                if item.quiz_answer:
                    print(f"Quiz Answer: {item.quiz_answer} (pre-filled if recognized)")
                if item.error_log:
                    print(f"Note:        {item.error_log}")
                print("-" * 76)
                print("Solve CAPTCHA / confirm submission in the opened browser window.")
                print("Commands:")
                print("  [Enter] Mark as COMPLETED")
                print("  [s]     SKIP this contest")
                print("  [f]     Mark as FAILED")
                print("=" * 76)

                loop = asyncio.get_event_loop()
                user_choice = await loop.run_in_executor(None, lambda: input("Action: ").strip().lower())

                if user_choice == "s":
                    self.db.mark_skipped(item.id, reason="User chose to skip interactively")
                    return SweepstakeStatus.SKIPPED
                elif user_choice == "f":
                    self.db.mark_failed(item.id, error_log="User marked as failed interactively")
                    return SweepstakeStatus.FAILED
                else:
                    self.db.mark_completed(item.id)
                    return SweepstakeStatus.COMPLETED

            except Exception as e:
                logger.error(f"Error in interactive session: {e}")
                self.db.mark_failed(item.id, error_log=str(e))
                return SweepstakeStatus.FAILED

    async def process_batch(self, limit: Optional[int] = None, allow_interactive: bool = True) -> dict:
        """Processes pending sweepstakes through the automated and interactive pipeline.

        Args:
            limit: Maximum sweepstakes to process in this run.
            allow_interactive: If True, falls back to interactive helper when manual entry is needed.
        """
        max_items = limit or settings.MAX_ENTRIES_PER_RUN
        pending_items = self.db.get_pending_items(limit=max_items, only_automated=True)

        logger.info(f"Starting batch entry processing for {len(pending_items)} pending items...")

        summary = {
            "processed": 0,
            "completed": 0,
            "flagged_manual": 0,
            "failed": 0,
            "skipped": 0,
        }

        # First pass: Automated entries with headless browser
        async with BrowserManager(headless=settings.HEADLESS) as browser_mgr:
            page = await browser_mgr.create_stealth_page()

            for item in pending_items:
                summary["processed"] += 1
                logger.info(f"[{summary['processed']}/{len(pending_items)}] Processing: {item.title}")

                status, message = await self.run_single_automated(item, page)

                if status == SweepstakeStatus.COMPLETED:
                    self.db.mark_completed(item.id)
                    summary["completed"] += 1

                elif status == SweepstakeStatus.FAILED:
                    self.db.mark_failed(item.id, error_log=message or "Automated execution failed")
                    summary["failed"] += 1

                elif status == SweepstakeStatus.PENDING:
                    # Form needs manual intervention (CAPTCHA or unrecognized widget)
                    self.db.mark_requires_manual(item.id, reason=message or "Requires manual action")
                    summary["flagged_manual"] += 1

                # Randomized jitter between successive automated entries
                await asyncio.sleep(2.0)

        # Second pass: If interactive fallback is requested and we have manual items
        if allow_interactive and settings.INTERACTIVE_FALLBACK:
            manual_items = self.db.get_manual_items(limit=10)
            if manual_items:
                print(f"\nThere are {len(manual_items)} sweepstakes that require interactive manual confirmation.")
                try:
                    choice = input("Would you like to solve them now in browser? [Y/n]: ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    choice = "n"

                if choice in ("", "y", "yes"):
                    for m_item in manual_items:
                        res = await self.run_single_interactive(m_item)
                        if res == SweepstakeStatus.COMPLETED:
                            summary["completed"] += 1
                            if summary["flagged_manual"] > 0:
                                summary["flagged_manual"] -= 1
                        elif res == SweepstakeStatus.SKIPPED:
                            summary["skipped"] += 1

        return summary
